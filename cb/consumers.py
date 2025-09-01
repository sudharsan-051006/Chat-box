import json
import random
from channels.generic.websocket import AsyncWebsocketConsumer

# Helper function to generate a random color
def random_color():
    return "#" + "".join([random.choice("0123456789ABCDEF") for _ in range(6)])

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f"chat_{self.room_name}"

        # Assign a random username and color for this session
        self.user_name = f"User{random.randint(1000, 9999)}"
        self.user_color = random_color()

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

        # Send a system message to this user about their name/color
        await self.send(text_data=json.dumps({
            "message": f"You joined as {self.user_name}",
            "user": "System",
            "color": "#888888"
        }))

    async def disconnect(self, close_code):
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    # Receive message from WebSocket
    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get('message', '').strip()
        if not message:
            return

        # Broadcast to room group
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'user': self.user_name,
                'color': self.user_color
            }
        )

    # Receive message from room group
    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'message': event['message'],
            'user': event['user'],
            'color': event['color']
        }))