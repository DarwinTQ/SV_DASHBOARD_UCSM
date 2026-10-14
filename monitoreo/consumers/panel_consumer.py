"""Consumer WebSocket del panel en tiempo real (sección 10)."""
from channels.generic.websocket import AsyncJsonWebsocketConsumer

GRUPO_PANEL = "panel"


class PanelConsumer(AsyncJsonWebsocketConsumer):
    """Rechaza usuarios no autenticados; une a los demás al grupo 'panel'."""

    async def connect(self):
        usuario = self.scope.get("user")
        if usuario is None or not usuario.is_authenticated:
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(GRUPO_PANEL, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        await self.channel_layer.group_discard(GRUPO_PANEL, self.channel_name)

    async def mensaje_panel(self, evento: dict) -> None:
        """Handler invocado por NotificadorTiempoReal vía group_send."""
        await self.send_json({"tipo": evento["tipo"], "data": evento["data"]})
