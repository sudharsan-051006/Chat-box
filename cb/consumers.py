import json
import random
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from cb.models import Room

ROOM_USERS = {}
ROOM_TIMERS = {}


class ChatConsumer(AsyncWebsocketConsumer):

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
    def is_user_allowed(self, room, user):
        return room.allowed_users.filter(id=user.id).exists()

    @database_sync_to_async
    def refresh_room(self, room_id):
        return Room.objects.get(id=room_id)

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f"chat_{self.room_name}"

        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close()
            return

        room = await self.get_room(self.room_name)
        if not room:
            await self.close()
            return

        # ✅ always refresh DB instance to get updated allowed_users
        room = await self.refresh_room(room.id)

        is_allowed = await self.is_user_allowed(room, user)

        if room.is_locked:
            # ✅ User must already be allowed
            if not is_allowed:
                await self.close()
                return
        else:
            # ✅ When unlocked, add user to allowed list
            if not is_allowed:
                await self.add_allowed_user(room, user)
                room = await self.refresh_room(room.id)

        # Store user info
        self.user_name = user.username
        self.user_color = random.choice(["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"])

        # Track online user list in memory
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # Cancel deletion timer if someone joined back
        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # Broadcast "user joined"
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name}
        )

    async def disconnect(self, close_code):
        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name}
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # Auto-delete empty room
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(self.delete_room_after_timeout())

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)

        if not ROOM_USERS.get(self.room_group_name):
            try:
                await database_sync_to_async(Room.objects.filter(name=self.room_name).delete)()
                print(f"🗑️ Room '{self.room_name}' deleted (empty for 30s)")
            except Exception as e:
                print(f"⚠️ Room deletion error: {e}")

            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()

        if not message:
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": message,
                "user": self.user_name,
                "color": self.user_color,
            }
        )

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": event["message"],
            "user": event["user"],
            "color": event["color"],
        }))

    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": f"{event['user']} joined the chat 👋",
            "user": "System",
            "color": "#888888",
        }))
        await self.send_user_list()

    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": f"{event['user']} left the chat 👋",
            "user": "System",
            "color": "#888888",
        }))
        await self.send_user_list()

    async def send_user_list(self):
        users = ROOM_USERS.get(self.room_group_name, [])
        await self.send(text_data=json.dumps({
            "type": "user_list",
            "users": users,
        }))
