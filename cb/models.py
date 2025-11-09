from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()

class Room(models.Model):
    name = models.CharField(max_length=255, unique=True)
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, null=True)
    is_locked = models.BooleanField(default=False)

    # ⚡️ Instead of relying only on allowed_users M2M, also keep usernames
    allowed_usernames = models.JSONField(default=list, blank=True)

    allowed_users = models.ManyToManyField(User, blank=True, related_name="rooms_allowed")

    def __str__(self):
        return self.name
