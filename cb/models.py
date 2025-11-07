from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class Room(models.Model):
    name = models.CharField(max_length=255, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_rooms")
    is_locked = models.BooleanField(default=False)
    allowed_users = models.ManyToManyField(User, related_name="joined_rooms", blank=True)
    
    def __str__(self):
        return self.name
