"""
ASGI config for chatbox project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
import cb.routing  # ✅ import your app’s routing.py

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatbox.settings')

# Main ASGI application
application = ProtocolTypeRouter({
    "http": get_asgi_application(),   # Django views
    "websocket": AuthMiddlewareStack(  # WebSocket handler
        URLRouter(
            cb.routing.websocket_urlpatterns   # from cb/routing.py
        )
    ),
})
