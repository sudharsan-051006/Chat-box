import json
import random
import asyncio
import time
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from cb.models import Room
from cb.huffman_codec import encode_text, decode_text

# Track online users in memory
ROOM_USERS = {}
ROOM_TIMERS = {}
LAST_SEEN = {}


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
        """Add user to allowed list and save immediately."""
        room.allowed_users.add(user)
        room.save()

    @database_sync_to_async
    def user_is_allowed(self, room_name, user):
        """Check directly from DB if user is allowed."""
        return Room.objects.filter(
            name=room_name, allowed_users__id=user.id
        ).exists()

    @database_sync_to_async
    def lock_room_with_users(self, room_name, usernames):
        """When room locked, add all current online users to allowed list."""
        from django.contrib.auth.models import User
        try:
            room = Room.objects.get(name=room_name)
            for username in usernames:
                try:
                    user = User.objects.get(username=username)
                    room.allowed_users.add(user)
                except User.DoesNotExist:
                    pass
            room.is_locked = True
            room.save()
        except Room.DoesNotExist:
            pass

    @database_sync_to_async
    def get_allowed_usernames(self, room_name):
        """For debugging or display."""
        try:
            room = Room.objects.get(name=room_name)
            return list(room.allowed_users.values_list("username", flat=True))
        except Room.DoesNotExist:
            return []

    # ---------------------- Connection Logic ----------------------

    async def connect(self):
        """Handle WebSocket connection."""
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

        # Refresh latest DB state
        await database_sync_to_async(room.refresh_from_db)()

        # Check if allowed
        is_allowed = await self.user_is_allowed(self.room_name, user)
        print(f"[DEBUG] {user.username} allowed: {is_allowed}, locked: {room.is_locked}")

        if room.is_locked and not is_allowed:
            print(f"[DEBUG] ❌ {user.username} blocked (room locked)")
            await self.close(code=403)
            return

        # Auto-add user if room unlocked
        if not room.is_locked and not is_allowed:
            await self.add_allowed_user(room, user)

        # Assign user name + color
        self.user_name = user.username
        self.user_color = random.choice(
            ["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"]
        )

        # Record connection time
        LAST_SEEN[(self.room_group_name, self.user_name)] = time.time()

        # Track online users
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # Cancel any pending deletion timer
        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        # Join group
        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Notify others
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name},
        )

    async def disconnect(self, close_code):
        """Handle user disconnect."""
        if not self.user_name:
            return

        # Ignore disconnect if same user reconnected quickly
        last_seen = LAST_SEEN.get((self.room_group_name, self.user_name), 0)
        if time.time() - last_seen < 3:
            print(f"[DEBUG] Ignored disconnect for {self.user_name} (refresh)")
            return

        print(f"[DEBUG] {self.user_name} disconnected")

        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # If room empty, start timer for deletion
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(
                self.delete_room_after_timeout()
            )

    async def delete_room_after_timeout(self):
        """Delete room if empty for 30 seconds."""
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            await database_sync_to_async(
                Room.objects.filter(name=self.room_name).delete
            )()
            print(f"🗑 Deleted room {self.room_name} (empty for 30s)")
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    # ---------------------- Custom Lock Command ----------------------

    async def receive(self, text_data):
        """Receive messages or commands from client."""
        data = json.loads(text_data)
        message = data.get("message", "").strip()
        command = data.get("command", None)

        # 🔒 Handle lock command
        if command == "lock_room":
            online_users = ROOM_USERS.get(self.room_group_name, [])
            await self.lock_room_with_users(self.room_name, online_users)
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "system_message",
                    "message": f"🔒 Room locked! Allowed users: {', '.join(online_users)}",
                },
            )
            return

        # Normal chat message
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

    async def chat_message(self, event):
        decoded = decode_text(event["message"], event["codes"])
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": decoded,
            "user": event["user"],
            "color": event["color"],
        }))

    # ---------------------- System Events ----------------------

    async def system_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": "System",
            "message": event["message"],
        }))

    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": "System",
            "message": f"{event['user']} joined 👋",
        }))
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": "System",
            "message": f"{event['user']} left 👋",
        }))
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    # ---------------------- Online User List ----------------------

    async def update_all_user_lists(self):
        users = ROOM_USERS.get(self.room_group_name, [])
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "broadcast_user_list", "users": users},
        )

    async def broadcast_user_list(self, event):
        await self.send(text_data=json.dumps({
            "type": "user_list",
            "users": event["users"],
        }))
