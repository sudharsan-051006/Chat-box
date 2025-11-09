import json
import random
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from cb.models import Room
from cb.huffman_codec import encode_text, decode_text

ROOM_USERS = {}
ROOM_TIMERS = {}


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
        """Add user to allowed list and force DB commit."""
        room.allowed_users.add(user)
        room.save()  # ensure commit

    @database_sync_to_async
    def user_is_allowed(self, room_name, user):
        """Direct DB query – always accurate."""
        return Room.objects.filter(
            name=room_name, allowed_users__id=user.id
        ).exists()

    @database_sync_to_async
    def get_allowed_usernames(self, room_name):
        """Used only for debugging output."""
        from cb.models import Room
        try:
            r = Room.objects.get(name=room_name)
            return list(r.allowed_users.values_list("username", flat=True))
        except Room.DoesNotExist:
            return []

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

        # always refresh latest DB values
        await database_sync_to_async(room.refresh_from_db)()

        # true database check
        is_allowed = await self.user_is_allowed(self.room_name, user)
        allowed_usernames = await self.get_allowed_usernames(self.room_name)
        print(
            f"[DEBUG] room={self.room_name} | locked={room.is_locked} | "
            f"user={user.username} | allowed_in_db={is_allowed} | "
            f"db_allowed={allowed_usernames}"
        )

        # if room locked -> only already-allowed users can join
        if room.is_locked and not is_allowed:
            print(f"[DEBUG] {user.username} rejected (room locked)")
            await self.close(code=403)
            return

        # if room unlocked -> auto-add user to allowed list
        if not room.is_locked and not is_allowed:
            await self.add_allowed_user(room, user)
            print(f"[DEBUG] {user.username} auto-added to allowed list")

        # Assign UI color
        self.user_name = user.username
        self.user_color = random.choice(
            ["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"]
        )

        # Track active users
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # cancel pending deletion
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

        await asyncio.sleep(1)  # avoid flicker

        if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].remove(self.user_name)

        await self.channel_layer.group_send(
            self.room_group_name,
            {"type": "user_leave", "user": self.user_name},
        )

        await self.channel_layer.group_discard(self.room_group_name, self.channel_name)

        if not ROOM_USERS.get(self.room_group_name):
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(
                self.delete_room_after_timeout()
            )

    async def delete_room_after_timeout(self):
        await asyncio.sleep(30)
        if not ROOM_USERS.get(self.room_group_name):
            await database_sync_to_async(
                Room.objects.filter(name=self.room_name).delete
            )()
            print(f"🗑 Room {self.room_name} deleted (empty 30 s)")
            ROOM_USERS.pop(self.room_group_name, None)
            ROOM_TIMERS.pop(self.room_group_name, None)

    # ---------------------- Message Handling ----------------------

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()
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

    async def user_join(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "system",
                    "user": "System",
                    "message": f"{event['user']} joined 👋",
                }
            )
        )
        await asyncio.sleep(0.1)
        await self.update_all_user_lists()

    async def user_leave(self, event):
        await self.send(
            text_data=json.dumps(
                {
                    "type": "system",
                    "user": "System",
                    "message": f"{event['user']} left 👋",
                }
            )
        )
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
