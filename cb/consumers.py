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

# In-memory runtime state
ROOM_USERS = {}     # { group_name: [username, ...] }
ROOM_TIMERS = {}    # { group_name: asyncio.Task }
LAST_SEEN = {}      # { (room_name, username) : last_seen_timestamp }


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
    def add_allowed_user(self, room, user):
        """Add user to room.allowed_users and force commit."""
        room.allowed_users.add(user)
        room.save()

    @database_sync_to_async
    def user_is_allowed(self, room_name, user_id):
        """Direct DB check to avoid relation caching issues."""
        return Room.objects.filter(
            name=room_name, allowed_users__id=user_id
        ).exists()

    @database_sync_to_async
    def get_allowed_usernames(self, room_name):
        try:
            r = Room.objects.get(name=room_name)
            return list(r.allowed_users.values_list("username", flat=True))
        except Room.DoesNotExist:
            return []

    @database_sync_to_async
    def lock_room_with_users(self, room_name, usernames):
        """
        Add current online usernames to room.allowed_users and lock the room.
        usernames: list of username strings
        """
        try:
            room = Room.objects.get(name=room_name)
        except Room.DoesNotExist:
            return False

        for uname in usernames:
            try:
                user = User.objects.get(username=uname)
                room.allowed_users.add(user)
            except User.DoesNotExist:
                # skip if username not in DB
                continue

        room.is_locked = True
        room.save()
        return True

    # ---------------------- Connection Logic ----------------------

    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"
        user = self.scope["user"]

        # require authenticated user
        if not user.is_authenticated:
            await self.close(code=403)
            return

        # get room from DB
        room = await self.get_room(self.room_name)
        if not room:
            await self.close(code=404)
            return

        # refresh DB state of room
        await database_sync_to_async(room.refresh_from_db)()

        # DB-based permission check
        is_allowed = await self.user_is_allowed(self.room_name, user.id)
        allowed_usernames = await self.get_allowed_usernames(self.room_name)
        print(
            f"[DEBUG] room={self.room_name} locked={room.is_locked} user={user.username} "
            f"allowed_in_db={is_allowed} db_allowed={allowed_usernames}"
        )

        # if locked and not allowed -> reject
        if room.is_locked and not is_allowed:
            print(f"[DEBUG] {user.username} rejected (room locked)")
            await self.close(code=403)
            return

        # if unlocked and not already allowed -> auto-add to allowed_users
        if not room.is_locked and not is_allowed:
            # this will add user to DB allowed_users
            await self.add_allowed_user(room, user)
            print(f"[DEBUG] {user.username} auto-added to allowed list")

        # set instance-level name/color
        self.user_name = user.username
        self.user_color = random.choice(
            ["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"]
        )

        # mark last seen (for refresh detection)
        LAST_SEEN[(self.room_name, self.user_name)] = time.time()

        # track active users in memory (keyed by group name)
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # cancel pending deletion if any
        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        # join channel group & accept
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # notify join to group
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name},
        )

    async def disconnect(self, close_code):
        # nothing to do if no username (not fully connected)
        if not self.user_name:
            return

        # ignore quick refresh disconnects (grace window)
        last_seen = LAST_SEEN.get((self.room_name, self.user_name), 0)
        if time.time() - last_seen < 3:
            print(f"[DEBUG] Ignored disconnect for {self.user_name} (likely refresh)")
            return

        print(f"[DEBUG] {self.user_name} disconnecting normally...")

        # remove from in-memory online list
        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        # notify group about leave
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )

        # leave group
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # if room now empty, schedule deletion
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(
                self.delete_room_after_timeout()
            )

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)
        # if still empty, delete from DB and clear in-memory state
        if not ROOM_USERS.get(self.room_group_name):
            # delete room in DB (if it still exists)
            await database_sync_to_async(Room.objects.filter(name=self.room_name).delete)()
            print(f"🗑 Room {self.room_name} deleted (empty 30 s)")
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    # ---------------------- Message Handling ----------------------

    async def receive(self, text_data):
        """
        Handle incoming messages. If the message JSON contains:
          - {"command": "lock_room"} -> lock room (DB): add current online users to allowed_users
          - otherwise treat as chat message {"message": "..."}
        """
        data = json.loads(text_data)
        command = data.get("command")
        message = data.get("message", "").strip()

        # handle lock command (add current online users -> DB allowed_users)
        if command == "lock_room":
            # current online usernames from in-memory list (group)
            online = ROOM_USERS.get(self.room_group_name, []).copy()
            # lock in DB: add users and set is_locked
            success = await self.lock_room_with_users(self.room_name, online)
            if success:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "system_message",
                        "message": f"🔒 Room locked! Allowed: {', '.join(online)}",
                    },
                )
            return

        # normal chat message
        if not message:
            return

        # compress via Huffman
        encoded_msg, codes = encode_text(message)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": encoded_msg,
                "codes": codes,
                "user": self.user_name,
                "color": self.user_color,
            },
        )

    async def chat_message(self, event):
        # decompress and forward to client
        decoded = decode_text(event["message"], event["codes"])
        await self.send(
            text_data=json.dumps(
                {
                    "type": "chat",
                    "message": decoded,
                    "user": event["user"],
                    "color": event["color"],
                }
            )
        )

    # ---------------------- System Events ----------------------

    async def system_message(self, event):
        await self.send(
            text_data=json.dumps(
                {"type": "system", "user": "System", "message": event["message"]}
            )
        )

    async def user_join(self, event):
        # notify user join
        await self.send(
            text_data=json.dumps(
                {"type": "system", "user": "System", "message": f"{event['user']} joined 👋"}
            )
        )
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    async def user_leave(self, event):
        await self.send(
            text_data=json.dumps(
                {"type": "system", "user": "System", "message": f"{event['user']} left 👋"}
            )
        )
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    # ---------------------- Online User List ----------------------

    async def update_all_user_lists(self):
        users = ROOM_USERS.get(self.room_group_name, [])
        await self.channel_layer.group_send(
            self.room_group_name, {"type": "broadcast_user_list", "users": users}
        )

    async def broadcast_user_list(self, event):
        await self.send(
            text_data=json.dumps({"type": "user_list", "users": event["users"]})
        )
