"""
ASGI config for sv_ucsm project.

Expone ``application`` como ``ProtocolTypeRouter`` (HTTP + WebSocket) para que
Daphne pueda servir tanto las vistas Django como el panel en tiempo real.
"""
import os

import django
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sv_ucsm.settings")
django.setup()
django_asgi_app = get_asgi_application()

from monitoreo import routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns)),
})
