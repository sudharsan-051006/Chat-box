import json
import random
import asyncio
import time
from channels.generic.websocket import AsyncWebsocketConsumer

# ---------------- In-Memory State ----------------
ROOM_USERS = {}              # { room_name: [online_usernames] }
ROOM_TIMERS = {}             # { room_name: asyncio.Task }
LOCKED_ALLOWED_USERS = {}    # { room_name: [permanent allowed usernames] }
LAST_SEEN = {}               # {(room_name, username): last_seen_time}


class ChatConsumer(AsyncWebsocketConsumer):
    user_name = None
    user_color = None

    # ---------------------- CONNECTION ----------------------
    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"
        user = self.scope["user"]

        if not user.is_authenticated:
            await self.close(code=403)
            return

        self.user_name = user.username.strip()
        self.user_color = random.choice(
            ["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"]
        )

        # If room locked → check allowed list
        allowed = LOCKED_ALLOWED_USERS.get(self.room_name)
        if allowed is not None and self.user_name not in allowed:
            print(f"[LOCKED] ❌ {self.user_name} blocked from {self.room_name}")
            await self.close(code=403)
            return

        # Track timestamp for refresh handling
        LAST_SEEN[(self.room_name, self.user_name)] = time.time()

        # Add to online list
        ROOM_USERS.setdefault(self.room_name, [])
        if self.user_name not in ROOM_USERS[self.room_name]:
            ROOM_USERS[self.room_name].append(self.user_name)

        # Cancel delete timer if needed
        if self.room_name in ROOM_TIMERS:
            ROOM_TIMERS[self.room_name].cancel()
            del ROOM_TIMERS[self.room_name]

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_join", "user": self.user_name},
        )

    async def disconnect(self, close_code):
        if not self.user_name:
            return

        last_seen = LAST_SEEN.get((self.room_name, self.user_name), 0)
        # Ignore quick refreshes (<3s)
        if time.time() - last_seen < 3:
            print(f"[REFRESH] Ignoring disconnect for {self.user_name}")
            return

        if self.room_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_name]:
            ROOM_USERS[self.room_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # If room empty → start deletion timer
        if not ROOM_USERS.get(self.room_name):
            ROOM_TIMERS[self.room_name] = asyncio.create_task(
                self.delete_room_after_timeout()
            )

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_name):
            print(f"🗑 Deleted room {self.room_name} (empty 30s)")
            ROOM_USERS.pop(self.room_name, None)
            LOCKED_ALLOWED_USERS.pop(self.room_name, None)
            ROOM_TIMERS.pop(self.room_name, None)

    # ---------------------- RECEIVE ----------------------
    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()
        command = data.get("command", "").strip()

        # 🔒 Handle locking command
        if command == "lock_room":
            users_now = ROOM_USERS.get(self.room_name, []).copy()
            LOCKED_ALLOWED_USERS[self.room_name] = users_now  # persistent allowlist
            print(f"[LOCKED] Room {self.room_name} locked for {users_now}")
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "system_message",
                    "message": f"🔒 Room locked! Allowed: {', '.join(users_now)}",
                },
            )
            return

        # normal chat message
        if not message:
            return

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                "type": "chat_message",
                "user": self.user_name,
                "color": self.user_color,
                "message": message,
            },
        )

    # ---------------------- EVENTS ----------------------
    async def chat_message(self, event):
        await self.send(json.dumps({
            "type": "chat",
            "user": event["user"],
            "color": event["color"],
            "message": event["message"],
        }))

    async def system_message(self, event):
        await self.send(json.dumps({
            "type": "system",
            "user": "System",
            "message": event["message"],
        }))

    async def user_join(self, event):
        await self.send(json.dumps({
            "type": "system",
            "user": "System",
            "message": f"{event['user']} joined 👋",
        }))
        await asyncio.sleep(0.1)
        await self.update_user_list()

    async def user_leave(self, event):
        await self.send(json.dumps({
            "type": "system",
            "user": "System",
            "message": f"{event['user']} left 👋",
        }))
        await asyncio.sleep(0.1)
        await self.update_user_list()

    # ---------------------- USER LIST ----------------------
    async def update_user_list(self):
        users = ROOM_USERS.get(self.room_name, [])
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "broadcast_user_list", "users": users},
        )

    async def broadcast_user_list(self, event):
        await self.send(json.dumps({
            "type": "user_list",
            "users": event["users"],
        }))
