import json
import random
import asyncio
import time
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from cb.models import Room
from cb.huffman_codec import encode_text, decode_text
from django.contrib.auth import get_user_model

User = get_user_model()

ROOM_USERS = {}     # { group_name: [username, ...] }
ROOM_TIMERS = {}
LAST_SEEN = {}
REACTIONS = {} 


class ChatConsumer(AsyncWebsocketConsumer):
    user_name = None
    user_color = None

    # ---------------------- Database Helpers ----------------------

    @database_sync_to_async
    def get_room(self, name):
        try:
            return Room.objects.get(name=name)
        except Room.DoesNotExist:
            return None

    @database_sync_to_async
    def add_allowed_username(self, room_name, username):
        """
        Add username to room.allowed_usernames (JSON) and sync M2M.
        Username should be normalized (lower, stripped) by caller.
        """
        room = Room.objects.get(name=room_name)
        # Ensure list exists and normalized
        if not isinstance(room.allowed_usernames, list):
            room.allowed_usernames = []

        if username not in room.allowed_usernames:
            room.allowed_usernames.append(username)
            room.allowed_usernames = list(dict.fromkeys(room.allowed_usernames))  # dedupe preserving order
            room.save()

        # Sync m2m so cb_room_allowed_users shows entries for users that exist
        # (silent skip if user not in auth_user)
        try:
            user = User.objects.get(username=username)
            room.allowed_users.add(user)
            room.save()
        except User.DoesNotExist:
            pass

    @database_sync_to_async
    def user_is_allowed(self, room_name, username):
        """Check by username inside allowed_usernames JSON."""
        try:
            room = Room.objects.get(name=room_name)
            allowed = room.allowed_usernames if isinstance(room.allowed_usernames, list) else []
            return username in allowed
        except Room.DoesNotExist:
            return False

    @database_sync_to_async
    def lock_room_with_usernames(self, room_name, usernames):
        """
        Lock room and add all usernames (normalized) to allowed_usernames,
        and sync M2M (if corresponding User exists).
        """
        try:
            room = Room.objects.get(name=room_name)
        except Room.DoesNotExist:
            return False

        if not isinstance(room.allowed_usernames, list):
            room.allowed_usernames = []

        # Normalize input usernames and merge
        normalized = [u.strip().lower() for u in usernames if u]
        combined = list(dict.fromkeys(room.allowed_usernames + normalized))  # dedupe
        room.allowed_usernames = combined
        room.is_locked = True
        room.save()

        # Sync M2M: add existing User records matching usernames
        for uname in normalized:
            try:
                u = User.objects.get(username=uname)
                room.allowed_users.add(u)
            except User.DoesNotExist:
                continue

        room.save()
        return True

    @database_sync_to_async
    def sync_allowed_user_m2m(self, room_name):
        """
        Ensure cb_room_allowed_users M2M table mirrors allowed_usernames JSON:
        - Adds any existing User objects for usernames in allowed_usernames
        - Optionally (we choose to) *do not* remove M2M entries for missing usernames,
          but you can remove extras if you want strict sync.
        """
        try:
            room = Room.objects.get(name=room_name)
        except Room.DoesNotExist:
            return

        allowed_usernames = room.allowed_usernames if isinstance(room.allowed_usernames, list) else []

        # Add missing M2M entries for users that exist
        for uname in allowed_usernames:
            uname_norm = uname.strip().lower()
            try:
                u = User.objects.get(username=uname_norm)
                room.allowed_users.add(u)
            except User.DoesNotExist:
                continue

        # save after changes
        room.save()

    # ---------------------- Connection Logic ----------------------

    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"

        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close(code=403)
            return

        room = await self.get_room(self.room_name)
        if not room:
            await self.close(code=404)
            return

        # ensure we have latest DB state
        await database_sync_to_async(room.refresh_from_db)()

        # normalize username for checks
        username = user.username.strip().lower()

        # check DB-based allowed_usernames
        is_allowed = await self.user_is_allowed(self.room_name, username)

        # debug output (useful in logs)
        # note: avoid heavy reads in production; this is useful for debugging
        print(f"[DEBUG] room={self.room_name} locked={room.is_locked} user={username} allowed={is_allowed} db_allowed={room.allowed_usernames}")

        # if room locked and user not allowed -> reject
        if room.is_locked and not is_allowed:
            print(f"[DEBUG] {username} blocked (room locked)")
            await self.close(code=403)
            return

        # if unlocked and not in allowed_usernames -> add (persist username + sync M2M)
        if not room.is_locked and not is_allowed:
            await self.add_allowed_username(self.room_name, username)
            # sync M2M too (function already attempts this)
            await self.sync_allowed_user_m2m(self.room_name)
            print(f"[DEBUG] {username} auto-added to allowed_usernames and synced M2M")

        # set instance attributes
        self.user_name = username
        self.user_color = random.choice(["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"])

        # mark last seen for quick refresh detection
        LAST_SEEN[(self.room_group_name, self.user_name)] = time.time()

        # update in-memory online list (keyed by group name)
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # cancel deletion timer if present
        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        # join group and accept connection
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # notify others
        await self.channel_layer.group_send(self.room_group_name, {"type": "user_join", "user": self.user_name})

    async def disconnect(self, close_code):
        if not self.user_name:
            return

        # ignore quick refreshes (grace window)
        last_seen = LAST_SEEN.get((self.room_group_name, self.user_name), 0)
        if time.time() - last_seen < 3:
            print(f"[DEBUG] Ignored disconnect for {self.user_name} (likely refresh)")
            return

        # remove from in-memory list
        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        # notify group and discard
        await self.channel_layer.group_send(self.room_group_name, {"type": "user_leave", "user": self.user_name})
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # if room empty -> schedule DB deletion after timeout
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(self.delete_room_after_timeout())

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            # delete DB room (if still exists)
            await database_sync_to_async(Room.objects.filter(name=self.room_name).delete)()
            print(f"🗑 Room {self.room_name} deleted (empty 30 s)")
            # clear in-memory state
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    # ---------------------- Message Handling ----------------------
    async def receive(self, text_data):
        """Handle incoming WebSocket messages (commands + chat)."""
        data = json.loads(text_data)
        message = data.get("message", "").strip()
        command = data.get("command")

        # ---------------------- LOCK ROOM COMMAND ----------------------
        if command == "lock_room":
            # Get current online users in this room
            online = ROOM_USERS.get(self.room_group_name, []).copy()

            # Normalize usernames (lowercase, strip spaces)
            online_norm = [u.strip().lower() for u in online if u]

            # Lock room in DB and save allowed_usernames
            success = await self.lock_room_with_usernames(self.room_name, online_norm)
            if success:
                # Ensure M2M sync (if your Room still uses allowed_users field)
                await self.sync_allowed_user_m2m(self.room_name)

                # Notify all connected users
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "system_message",
                        "message": f"🔒 Room locked! Allowed: {', '.join(online_norm)}"
                    }
                )
            return

        # ---------------------- NORMAL MESSAGE ----------------------
        if not message:
            return  # Ignore empty messages

        # Compress message using Huffman encoding
        encoded_msg, codes = encode_text(message)

        # Broadcast encoded message + metadata
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": encoded_msg,   # compressed string
                "codes": codes,           # dictionary for decoding
                "user": self.user_name,
                "color": self.user_color,
            },
        )

    async def chat_message(self, event):
        decoded = decode_text(event["message"], event["codes"])
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": decoded,
            "user": event["user"],
            "color": event["color"]
        }))

    # ---------------------- System Events ----------------------

    async def system_message(self, event):
        await self.send(text_data=json.dumps(event))

    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": "System",
            "message": f"{event['user']} joined 👋"
        }))
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": "System",
            "message": f"{event['user']} left 👋"
        }))
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    # ---------------------- Online User List ----------------------

    async def update_all_user_lists(self):
        users = ROOM_USERS.get(self.room_group_name, [])
        await self.channel_layer.group_send(self.room_group_name, {"type": "broadcast_user_list", "users": users})

    async def broadcast_user_list(self, event):
        await self.send(text_data=json.dumps({"type": "user_list", "users": event["users"]}))

