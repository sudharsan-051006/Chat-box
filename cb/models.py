from django.db import models
from django.contrib.auth.models import User

# Create your models here.
class Room(models.Model):
    name = models.CharField(max_length=100, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name="created_rooms")
    is_locked = models.BooleanField(default=False)

    def __str__(self):
        return self.name
