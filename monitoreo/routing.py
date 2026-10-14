"""Rutas WebSocket de la app monitoreo (sección 10)."""
from django.urls import re_path

from monitoreo.consumers.panel_consumer import PanelConsumer

websocket_urlpatterns = [
    re_path(r"^ws/panel/$", PanelConsumer.as_asgi()),
]
