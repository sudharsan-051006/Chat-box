import json
import random
import asyncio
import time
from channels.generic.websocket import AsyncWebsocketConsumer

# ---------------- GLOBAL STATE (in memory only) ----------------
ROOM_USERS = {}              # active users per room
ROOM_TIMERS = {}             # auto-delete timers
LAST_SEEN = {}               # for detecting refresh reconnects
LOCKED_ALLOWED_USERS = {}    # permanent allowed users after locking


class ChatConsumer(AsyncWebsocketConsumer):
    user_name = None
    user_color = None

    # ---------------------- CONNECTION LOGIC ----------------------

    async def connect(self):
        self.room_name = self.scope["url_route"]["kwargs"]["room_name"]
        self.room_group_name = f"chat_{self.room_name}"
        user = self.scope["user"]

        # must be logged in
        if not user.is_authenticated:
            await self.close(code=403)
            return

        self.user_name = user.username
        self.user_color = random.choice(
            ["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"]
        )

        # if room locked → only allowed users can join
        locked_users = LOCKED_ALLOWED_USERS.get(self.room_group_name)
        if locked_users is not None and self.user_name not in locked_users:
            print(f"[LOCKED] ❌ {self.user_name} blocked from {self.room_name}")
            await self.close(code=403)
            return

        # mark connection time
        LAST_SEEN[(self.room_group_name, self.user_name)] = time.time()

        # add to online users
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # cancel deletion timer if present
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

        # ignore quick refresh reconnects
        last = LAST_SEEN.get((self.room_group_name, self.user_name), 0)
        if time.time() - last < 3:
            print(f"[REFRESH] ignored disconnect of {self.user_name}")
            return

        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        # if no users left → schedule deletion
        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(
                self.delete_room_after_timeout()
            )

    async def delete_room_after_timeout(self):
        """delete empty room after 30s"""
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            print(f"🗑 Deleted room {self.room_name} (empty 30 s)")
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)
            LOCKED_ALLOWED_USERS.pop(self.room_group_name, None)

    # ---------------------- MESSAGE HANDLING ----------------------

    async def receive(self, text_data):
        data = json.loads(text_data)

        # ----- Handle Lock Command -----
        if data.get("command") == "lock_room":
            online = ROOM_USERS.get(self.room_group_name, []).copy()
            LOCKED_ALLOWED_USERS[self.room_group_name] = online
            print(f"[LOCKED] Room {self.room_name} locked for users: {online}")
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    "type": "system_message",
                    "message": f"🔒 Room locked! Allowed: {', '.join(online)}",
                },
            )
            return

        # ----- Handle Normal Message -----
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
            },
        )

    async def chat_message(self, event):
        await self.send(json.dumps({
            "type": "chat",
            "message": event["message"],
            "user": event["user"],
            "color": event["color"],
        }))

    # ---------------------- SYSTEM EVENTS ----------------------

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
        users = ROOM_USERS.get(self.room_group_name, [])
        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "broadcast_user_list", "users": users},
        )

    async def broadcast_user_list(self, event):
        await self.send(json.dumps({
            "type": "user_list",
            "users": event["users"],
        }))
