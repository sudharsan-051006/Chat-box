import json
import random
import asyncio
import traceback
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from cb.models import Room

ROOM_USERS = {}     # { "chat_roomname": ["alice","bob"] }
ROOM_TIMERS = {}    # { "chat_roomname": asyncio.Task }


class ChatConsumer(AsyncWebsocketConsumer):

    @database_sync_to_async
    def get_room(self, name):
        try:
            return Room.objects.get(name=name)
        except Room.DoesNotExist:
            return None

    @database_sync_to_async
    def add_allowed_user(self, room, user):
        # add user to allowed_users m2m (no-op if already present)
        room.allowed_users.add(user)

    @database_sync_to_async
    def is_user_allowed(self, room, user):
        return room.allowed_users.filter(id=user.id).exists()

    @database_sync_to_async
    def room_is_locked(self, name):
        # re-fetch current lock state
        r = Room.objects.filter(name=name).values_list("is_locked", flat=True).first()
        return bool(r)

    @database_sync_to_async
    def delete_room_by_name(self, name):
        return Room.objects.filter(name=name).delete()

    async def connect(self):
        self.room_name = self.scope['url_route']['kwargs']['room_name']
        self.room_group_name = f"chat_{self.room_name}"
        user = self.scope.get("user")

        if not user or not user.is_authenticated:
            # unauthenticated clients cannot join
            await self.close()
            return

        # fetch room from DB
        room = await self.get_room(self.room_name)
        if not room:
            await self.close()
            return

        # check allowed status (always check DB)
        already_allowed = await self.is_user_allowed(room, user)

        # If locked and user not allowed → deny
        if room.is_locked and not already_allowed:
            # blocked
            await self.close()
            return

        # If unlocked and not already allowed → add to allowed_users (persist)
        if not room.is_locked and not already_allowed:
            await self.add_allowed_user(room, user)

        # store user info
        self.user_name = user.username
        self.user_color = random.choice(["#3498db", "#e67e22", "#2ecc71", "#9b59b6", "#e74c3c"])

        # add to in-memory online list
        ROOM_USERS.setdefault(self.room_group_name, [])
        if self.user_name not in ROOM_USERS[self.room_group_name]:
            ROOM_USERS[self.room_group_name].append(self.user_name)

        # cancel pending delete timer (someone rejoined)
        if self.room_group_name in ROOM_TIMERS:
            task = ROOM_TIMERS.pop(self.room_group_name)
            task.cancel()

        await self.channel_layer.group_add(self.room_group_name, self.channel_name)
        await self.accept()

        # notify the room
        await self.channel_layer.group_send(self.room_group_name, {"type": "user_join", "user": self.user_name})

    async def disconnect(self, close_code):
        try:
            if self.room_group_name in ROOM_USERS and self.user_name in ROOM_USERS[self.room_group_name]:
                ROOM_USERS[self.room_group_name].remove(self.user_name)
        except Exception:
            pass

        # broadcast leave
        try:
            await self.channel_layer.group_send(self.room_group_name, {"type": "user_leave", "user": self.user_name})
        except Exception:
            pass

        try:
            await self.channel_layer.group_discard(self.room_group_name, self.channel_name)
        except Exception:
            pass

        # if room becomes empty, schedule deletion task (30s)
        if not ROOM_USERS.get(self.room_group_name):
            # create and store the deletion task
            ROOM_TIMERS[self.room_group_name] = asyncio.create_task(self._schedule_room_delete())

    async def _schedule_room_delete(self):
        # keep the room_name and group for the task closure
        group = self.room_group_name
        name = self.room_name

        try:
            await asyncio.sleep(30)  # wait before deleting
            # if someone joined in meantime, cancel deletion
            if ROOM_USERS.get(group):
                return

            # re-check room lock state from DB — do NOT delete locked rooms
            locked = await self.room_is_locked(name)
            if locked:
                # Do not delete locked rooms — keep them (owner may want them)
                print(f"🛑 Not deleting locked room '{name}'")
                return

            # Delete the Room row if still empty and unlocked
            deleted = await self.delete_room_by_name(name)
            print(f"🗑️ Deleted room '{name}', result: {deleted}")

            # clean up memory structures
            ROOM_USERS.pop(group, None)
            ROOM_TIMERS.pop(group, None)
        except asyncio.CancelledError:
            # task cancelled because someone rejoined
            return
        except Exception:
            print("Error in _schedule_room_delete:", traceback.format_exc())
            ROOM_USERS.pop(group, None)
            ROOM_TIMERS.pop(group, None)

    async def receive(self, text_data):
        data = json.loads(text_data)
        message = data.get("message", "").strip()
        if not message:
            return

        await self.channel_layer.group_send(self.room_group_name, {
            "type": "chat_message",
            "message": message,
            "user": self.user_name,
            "color": self.user_color,
        })

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
