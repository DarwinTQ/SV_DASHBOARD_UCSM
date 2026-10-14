import asyncio

from channels.testing import WebsocketCommunicator
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase

from monitoreo.consumers.panel_consumer import PanelConsumer
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal


class PanelConsumerTests(TestCase):
    async def test_rechaza_usuarios_anonimos(self):
        comunicador = WebsocketCommunicator(PanelConsumer.as_asgi(), "/ws/panel/")
        comunicador.scope["user"] = AnonymousUser()
        conectado, _ = await comunicador.connect()
        self.assertFalse(conectado)
        await comunicador.disconnect()

    async def test_usuario_autenticado_recibe_incidencia_nueva(self):
        User = get_user_model()
        usuario = await User.objects.acreate(username="probador", password="x")

        comunicador = WebsocketCommunicator(PanelConsumer.as_asgi(), "/ws/panel/")
        comunicador.scope["user"] = usuario
        conectado, _ = await comunicador.connect()
        self.assertTrue(conectado)

        # NotificadorTiempoReal usa async_to_sync; en producción siempre se llama
        # desde un hilo de worker (no desde el event loop), así que aquí lo
        # replicamos con asyncio.to_thread en lugar de llamarlo directamente.
        await asyncio.to_thread(NotificadorTiempoReal.incidencia_nueva, {"id": 1, "tipo": "PERSONA"})

        mensaje = await comunicador.receive_json_from(timeout=2)
        self.assertEqual(mensaje["tipo"], "incidencia_nueva")
        self.assertEqual(mensaje["data"]["id"], 1)

        await comunicador.disconnect()
