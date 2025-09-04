from django.contrib.auth.forms import UserCreationForm
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.shortcuts import render

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