"""
ASGI config for sv_ucsm project.

Expone ``application`` como ``ProtocolTypeRouter`` (HTTP + WebSocket) para que
Daphne pueda servir tanto las vistas Django como el panel en tiempo real.
"""
import os

import django
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.conf import settings
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sv_ucsm.settings")
django.setup()
django_asgi_app = get_asgi_application()

# En desarrollo Daphne tiene que servir /static/ él mismo: la app ASGI de Django
# no lo hace (eso es cosa de runserver, que aquí no se usa) y WhiteNoise solo
# actúa con DEBUG=False. Sin esto los estáticos vendrían de la copia recolectada
# en staticfiles/ y quedarían desactualizados hasta el siguiente collectstatic.
if settings.DEBUG:
    django_asgi_app = ASGIStaticFilesHandler(django_asgi_app)

from monitoreo import routing  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns)),
})
