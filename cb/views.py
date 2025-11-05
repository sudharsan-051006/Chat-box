from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from .models import Room


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
@login_required(login_url="/login/")
def room(request, room_name):
    """Join a specific chat room."""
    return render(request, 'cb/room.html', {'room_name': room_name})
