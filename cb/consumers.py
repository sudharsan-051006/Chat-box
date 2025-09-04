import json
import random
from channels.generic.websocket import AsyncWebsocketConsumer

# Keep track of online users per room
ROOM_USERS = {}

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f"chat_{self.room_name}"

        # Get logged-in user
        user = self.scope["user"]
        if not user.is_authenticated:
            await self.close()
            return

        self.user_name = user.username
        # Random color per user
        self.user_color = random.choice(["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"])

        # Add user to room list
        if self.room_group_name not in ROOM_USERS:
            ROOM_USERS[self.room_group_name] = []
        ROOM_USERS[self.room_group_name].append(self.user_name)

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        await self.accept()

        # Notify all users about new user
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "user_join",
                "user": self.user_name,
            }
        )

    async def disconnect(self, close_code):
        # Remove user from online list
        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        # Notify users about leaving
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "user_leave",
                "user": self.user_name,
            }
        )

        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()
        if not message:
            return

        # Broadcast message to room
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "message": message,
                "user": self.user_name,
                "color": self.user_color
            }
        )

    # Send chat message to WebSocket
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": event["message"],
            "user": event["user"],
            "color": event["color"]
        }))

    # Handle user joining
    async def user_join(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": f"{event['user']} joined the chat 👋",
            "user": "System",
            "color": "#888888"
        }))
        # Send updated user list
        await self.send_user_list()

    # Handle user leaving
    async def user_leave(self, event):
        await self.send(text_data=json.dumps({
            "type": "chat",
            "message": f"{event['user']} left the chat 👋",
            "user": "System",
            "color": "#888888"
        }))
        # Send updated user list
        await self.send_user_list()

    # Send the current online users list
    async def send_user_list(self):
        users = ROOM_USERS.get(self.room_group_name, [])
        await self.send(text_data=json.dumps({
            "type": "user_list",
            "users": users
        }))