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


# 💬 JOIN SPECIFIC ROOM
@login_required
def room(request, room_name):
    room = Room.objects.get(name=room_name)

    # ✅ Prevent new users if room is locked
    if room.is_locked and request.user != room.created_by:
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
    room = get_object_or_404(Room, name=room_name)

    if room.created_by != request.user:
        return HttpResponseForbidden("❌ You are not allowed to lock this room.")

    room.is_locked = not room.is_locked
    room.save()
    msg = "🔒 Room locked" if room.is_locked else "🔓 Room unlocked"
    messages.success(request, msg)
    return HttpResponseRedirect(reverse('room', args=[room_name]))


def run_migrations(request):
    try:
        call_command('makemigrations', 'cb')
        call_command('migrate')
        return HttpResponse("✅ Migrations applied successfully.")
    except Exception as e:
        return HttpResponse(f"❌ Migration error: {str(e)}")
