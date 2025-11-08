import json
import random
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from cb.models import Room
from cb.huffman_codec import encode_text, decode_text  # ✅ Huffman codec

ROOM_USERS = {}     # online users in each room
ROOM_TIMERS = {}    # auto-delete timers for empty rooms


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
        room.allowed_users.add(user)

    @database_sync_to_async
    def user_is_allowed(self, room, user):
        return room.allowed_users.filter(id=user.id).exists()

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

        is_allowed = await self.user_is_allowed(room, user)

        # ✅ Allow reconnects if user was already allowed before lock
        if room.is_locked and not is_allowed:
            await self.close(code=403)
            return

        # ✅ Auto-add new users only if room unlocked
        if not room.is_locked and not is_allowed:
            await self.add_allowed_user(room, user)

        # Assign name + color
        self.user_name = user.username
        self.user_color = random.choice(
            ["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"]
        )

        # Track active users
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # Cancel deletion timer if room was empty earlier
        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Notify join
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name},
        )

    async def disconnect(self, close_code):
        if not self.user_name:
            return

        # Small delay avoids “left + joined” flicker on quick refresh
        await asyncio.sleep(1)

        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # Auto-delete if room empty
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(
                self.delete_room_after_timeout()
            )

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            await database_sync_to_async(Room.objects.filter(name=self.room_name).delete)()
            print(f"🗑 Room {self.room_name} deleted (empty for 30s)")
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    # ---------------------- Message Handling ----------------------

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()

        if not message:
            return

        # ✅ Compress message via Huffman Encoding
        encoded_msg, codes = encode_text(message)

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": encoded_msg,   # compressed string
                "codes": codes,           # dict for decoding
                "user": self.user_name,
                "color": self.user_color,
            },
        )

    async def chat_message(self, event):
        encoded = event["message"]
        codes = event["codes"]
        user = event["user"]
        color = event["color"]

        # ✅ Decompress message
        decoded = decode_text(encoded, codes)

        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": decoded,
            "user": user,
            "color": color,
        }))

    # ---------------------- System Events ----------------------

    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": event["user"],
            "message": f"{event['user']} joined 👋",
        }))
        await self.send_user_list()

    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            "type": "system",
            "user": event["user"],
            "message": f"{event['user']} left 👋",
        }))
        await self.send_user_list()

    async def send_user_list(self):
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "broadcast_user_list",
                "users": ROOM_USERS.get(self.room_group_name, []),
            }
        )

    async def broadcast_user_list(self, event):
        await self.send(text_data=json.dumps({
            "type": "user_list",
            "users": event["users"],
        }))
