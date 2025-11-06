from django.urls import path
from django.contrib.auth import views as auth_views
from . import views
from django.shortcuts import redirect

urlpatterns = [
    path("signup/", views.signup, name="signup"),
    path("login/", auth_views.LoginView.as_view(template_name="cb/login.html"), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path('password_change/', auth_views.PasswordChangeView.as_view(template_name='cb/password_change.html'), name='password_change'),
    path('password_change/done/', auth_views.PasswordChangeDoneView.as_view(template_name='cb/password_change_done.html'), name='password_change_done'),

    # Chat system
    path('', views.index, name='index'),                         # 👈 Room list (create_room.html)
    path('create/', views.create_room, name='create_room'),       # 👈 Create new room
    path('chat/<str:room_name>/', views.room, name='room'), 
    path('run-migrate/', views.run_migrations, name='run_migrations'),
]
