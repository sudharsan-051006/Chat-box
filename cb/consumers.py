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

ROOM_USERS = {}      # { group_name: [username, ...] }
ROOM_TIMERS = {}     # { group_name: asyncio.Task }
LAST_SEEN = {}       # { (group_name, username): timestamp }
REACTIONS = {}       # { group_name: {"likes": int, "dislikes": int} }


class ChatConsumer(AsyncWebsocketConsumer):
    user_name = None
    user_color = None

    # ---------------------- Database Helpers ----------------------

    @database_sync_to_async
    def get_room(self, name):
        """Fetch a room object by name."""
        try:
            return Room.objects.get(name=name)
        except Room.DoesNotExist:
            return None

    @database_sync_to_async
    def add_allowed_username(self, room_name, username):
        """Add username to room.allowed_usernames (JSON) and sync M2M."""
        room = Room.objects.get(name=room_name)
        if not isinstance(room.allowed_usernames, list):
            room.allowed_usernames = []

        if username not in room.allowed_usernames:
            room.allowed_usernames.append(username)
            room.allowed_usernames = list(dict.fromkeys(room.allowed_usernames))  # dedupe
            room.save()

        try:
            user = User.objects.get(username=username)
            room.allowed_users.add(user)
            room.save()
        except User.DoesNotExist:
            pass

    @database_sync_to_async
    def user_is_allowed(self, room_name, username):
        """Check if user is allowed based on allowed_usernames JSON."""
        try:
            room = Room.objects.get(name=room_name)
            allowed = room.allowed_usernames if isinstance(room.allowed_usernames, list) else []
            return username in allowed
        except Room.DoesNotExist:
            return False

    @database_sync_to_async
    def lock_room_with_usernames(self, room_name, usernames):
        """Lock a room and persist all usernames to allowed_usernames."""
        try:
            room = Room.objects.get(name=room_name)
        except Room.DoesNotExist:
            return False

        if not isinstance(room.allowed_usernames, list):
            room.allowed_usernames = []

        normalized = [u.strip().lower() for u in usernames if u]
        combined = list(dict.fromkeys(room.allowed_usernames + normalized))
        room.allowed_usernames = combined
        room.is_locked = True
        room.save()

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
        """Ensure M2M matches allowed_usernames JSON."""
        try:
            room = Room.objects.get(name=room_name)
        except Room.DoesNotExist:
            return

        allowed_usernames = room.allowed_usernames if isinstance(room.allowed_usernames, list) else []

        for uname in allowed_usernames:
            uname_norm = uname.strip().lower()
            try:
                u = User.objects.get(username=uname_norm)
                room.allowed_users.add(u)
            except User.DoesNotExist:
                continue

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

        await database_sync_to_async(room.refresh_from_db)()
        username = user.username.strip().lower()
        is_allowed = await self.user_is_allowed(self.room_name, username)

        print(f"[DEBUG] room={self.room_name} locked={room.is_locked} user={username} allowed={is_allowed} db_allowed={room.allowed_usernames}")

        if room.is_locked and not is_allowed:
            print(f"[DEBUG] {username} blocked (room locked)")
            await self.close(code=403)
            return

        if not room.is_locked and not is_allowed:
            await self.add_allowed_username(self.room_name, username)
            await self.sync_allowed_user_m2m(self.room_name)
            print(f"[DEBUG] {username} auto-added and synced M2M")

        self.user_name = username
        self.user_color = random.choice(["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"])
        LAST_SEEN[(self.room_group_name, self.user_name)] = time.time()

        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name},
        )

    async def disconnect(self, close_code):
        if not self.user_name:
            return

        last_seen = LAST_SEEN.get((self.room_group_name, self.user_name), 0)
        if time.time() - last_seen < 3:
            print(f"[DEBUG] Ignored disconnect for {self.user_name} (likely refresh)")
            return

        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )
        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(self.delete_room_after_timeout())

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            await database_sync_to_async(Room.objects.filter(name=self.room_name).delete)()
            print(f"🗑 Room {self.room_name} deleted (empty 30s)")
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    # ---------------------- Message Handling ----------------------

    async def receive(self, text_data):
        """Handles all WebSocket messages."""
        data = json.loads(text_data)
        message = data.get("message", "").strip()
        command = data.get("command")
        reaction = data.get("reaction")

        # ✅ Like/Dislike broadcast
        if reaction in ["like", "dislike"]:
            REACTIONS.setdefault(self.room_group_name, {"likes": 0, "dislikes": 0})

            if reaction == "like":
                REACTIONS[self.room_group_name]["likes"] += 1
            elif reaction == "dislike":
                REACTIONS[self.room_group_name]["dislikes"] += 1

            counts = REACTIONS[self.room_group_name]

            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "reaction_update",
                    "likes": counts["likes"],
                    "dislikes": counts["dislikes"],
                },
            )
            return

        # ✅ Lock room
        if command == "lock_room":
            online_users = ROOM_USERS.get(self.room_group_name, [])
            success = await self.lock_room_with_usernames(self.room_name, online_users)
            if success:
                await self.channel_layer.group_send(
                    self.room_group_name,
                    {
                        "type": "system_message",
                        "message": f"🔒 Room locked! Allowed: {', '.join(online_users)}",
                    },
                )
            return

        # ✅ Chat messages
        if not message:
            return

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

    async def reaction_update(self, event):
        """Broadcast like/dislike counts to all users."""
        await self.send(
            text_data=json.dumps(
                {
                    "type": "reaction",
                    "likes": event["likes"],
                    "dislikes": event["dislikes"],
                }
            )
        )

    async def chat_message(self, event):
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
        await self.send(text_data=json.dumps(event))

    async def user_join(self, event):
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
