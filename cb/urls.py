from django.urls import path
from django.shortcuts import redirect
from . import views

urlpatterns = [
    path('', lambda request: redirect('chat/room1/')),  # ✅ fixed import
    path("chat/<str:room_name>/", views.room, name="room"),
]
