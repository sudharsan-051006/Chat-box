import json
import random
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from cb.models import Room

# Keep track of online users and room timers
ROOM_USERS = {}
ROOM_TIMERS = {}

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f"chat_{self.room_name}"

        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close()
            return

        self.user_name = user.username
        self.user_color = random.choice(["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"])

        # Initialize room user list
        ROOM_USERS.setdefault(self.room_group_name, [])
        ROOM_USERS[self.room_group_name].append(self.user_name)

        # Cancel any pending deletion timer if someone joins again
        if self.room_group_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_group_name].cancel()
            del ROOM_TIMERS[self.room_group_name]

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name}
        )

    async def disconnect(self, close_code):
        # Remove user from list
        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        # Notify others
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name}
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # If room is empty, start a deletion timer
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(self.delete_room_after_timeout())

    async def delete_room_after_timeout(self):
        """Wait 30 seconds, then delete the room if it's still empty."""
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            try:
                await asyncio.to_thread(Room.objects.filter(name=self.room_name).delete)
                print(f"🗑️ Room '{self.room_name}' deleted (empty for 30s)")
            except Exception as e:
                print(f"⚠️ Error deleting room {self.room_name}: {e}")
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
