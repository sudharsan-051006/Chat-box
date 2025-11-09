from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Room
from django.http import HttpResponse
from django.core.management import call_command
from django.contrib.auth.models import User
from django.http import HttpResponseForbidden
from django.shortcuts import get_object_or_404
from django.contrib import messages
from django.urls import reverse
from django.http import HttpResponseRedirect
from django.db import connection
from django.http import JsonResponse



# 🧱 SIGNUP VIEW
def signup(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()  # create new user
            return redirect("login")  # go to login page after signup
    else:
        form = UserCreationForm()
    return render(request, "cb/signup.html", {"form": form})


# 🏠 ROOM LIST PAGE (index = create_room.html)
@login_required(login_url="/login/")
def index(request):
    """Show all available chat rooms."""
    rooms = Room.objects.all()
    return render(request, 'cb/create_room.html', {'rooms': rooms})


# 🏗️ CREATE NEW ROOM
@login_required(login_url="/login/")
def create_room(request):
    """Create a new chat room if it doesn't exist."""
    if request.method == 'POST':
        room_name = request.POST.get('room_name')
        if room_name and not Room.objects.filter(name=room_name).exists():
            Room.objects.create(name=room_name)
        return redirect('room', room_name=room_name)
    return redirect('index')


@login_required
def room(request, room_name):
    room = Room.objects.get(name=room_name)
    username = request.user.username.strip().lower()

    # ✅ Use allowed_usernames (JSON) instead of M2M check
    allowed_list = room.allowed_usernames if isinstance(room.allowed_usernames, list) else []

    # 🚫 Block if locked and user is not in allowed list (except creator)
    if room.is_locked and username not in allowed_list and request.user != room.created_by:
        return HttpResponseForbidden("🚫 This room is locked by the creator.")

    return render(request, 'cb/room.html', {'room_name': room_name, 'room': room})


@login_required
def create_room(request):
    if request.method == 'POST':
        room_name = request.POST['room_name']
        room, created = Room.objects.get_or_create(
            name=room_name,
            defaults={'created_by': request.user}  # ✅ save creator
        )
        return redirect('room', room_name=room_name)

@login_required
def toggle_lock(request, room_name):
    if request.method == "POST":
        room = get_object_or_404(Room, name=room_name)
        if request.user == room.created_by:
            room.is_locked = not room.is_locked
            room.save()
            return JsonResponse({"status": "success", "locked": room.is_locked})
        else:
            return JsonResponse({"status": "error", "message": "You are not allowed to lock this room."}, status=403)
    else:
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=400)

# def fix_allowed_users_table(request):
#     """Create the missing cb_room_allowed_users table if it doesn't exist"""
#     try:
#         with connection.cursor() as cursor:
#             cursor.execute("""
#             CREATE TABLE IF NOT EXISTS cb_room_allowed_users (
#                 id SERIAL PRIMARY KEY,
#                 room_id INTEGER NOT NULL REFERENCES cb_room(id) ON DELETE CASCADE,
#                 user_id INTEGER NOT NULL REFERENCES auth_user(id) ON DELETE CASCADE,
#                 UNIQUE (room_id, user_id)
#             );
#             """)
#         return HttpResponse("✅ cb_room_allowed_users table created successfully!")
#     except Exception as e:
# #         return HttpResponse(f"❌ Error while creating table: {e}")
# def fix_allowed_usernames_column(request):
#     from django.db import connection
#     with connection.cursor() as cursor:
#         cursor.execute("ALTER TABLE cb_room ADD COLUMN IF NOT EXISTS allowed_usernames JSONB DEFAULT '[]';")
#     return HttpResponse("✅ allowed_usernames column fixed!")
