from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.shortcuts import render
from .models import Room

@login_required(login_url="/login/")
def room(request, room_name):
    return render(request, "cb/room.html", {"room_name": room_name})

def signup(request):
    if request.method == "POST":
        form = UserCreationForm(request.POST)
        if form.is_valid():
            form.save()  # ✅ creates the new user
            return redirect("login")  # after signup, go to login page
    else:
        form = UserCreationForm()
    return render(request, "cb/signup.html", {"form": form})



def index(request):
    """Show all available chat rooms."""
    rooms = Room.objects.all()
    return render(request, 'cb/create_room.html', {'rooms': rooms})  # 👈 path adjusted

def create_room(request):
    """Create a new chat room if it doesn't exist."""
    if request.method == 'POST':
        room_name = request.POST.get('room_name')
        if room_name and not Room.objects.filter(name=room_name).exists():
            Room.objects.create(name=room_name)
        return redirect('room', room_name=room_name)
    return redirect('index')

def room(request, room_name):
    """Open a specific chat room."""
    return render(request, 'cb/room.html', {'room_name': room_name})
