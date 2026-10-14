from django.test import TestCase

from monitoreo.bot.comandos.vinculacion import ComandoVincular
from monitoreo.bot.contexto import ContextoComando
from monitoreo.bot.limitador import LimitadorFrecuencia
from monitoreo.models import AccesoTelegram, Conductor, Vehiculo
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class ComandoVincularTests(TestCase):
    def setUp(self):
        self.conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        self.vehiculo = Vehiculo.objects.create(
            conductor=self.conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        LimitadorFrecuencia._instancia = None

    def _ctx(self, chat_id="111"):
        cliente = ClienteBotAPIFalso()
        return ContextoComando(chat_id=chat_id, cliente=cliente), cliente

    def test_vincular_correctamente(self):
        ctx, cliente = self._ctx()
        ctx.args = ["71234567", "987654321"]
        ctx.mensaje_id_origen = 55
        ComandoVincular().ejecutar(ctx)

        self.assertTrue(
            AccesoTelegram.objects.filter(vehiculo=self.vehiculo, chat_id="111", rol="PROPIETARIO", puede_controlar=True).exists(),
        )
        self.conductor.refresh_from_db()
        self.assertEqual(self.conductor.telegram_chat_id, "111")
        self.assertIn({"chat_id": "111", "message_id": 55}, cliente.mensajes_borrados)
        self.assertTrue(any("Vinculado" in m["texto"] for m in cliente.mensajes_enviados))

    def test_vincular_acepta_telefono_sin_prefijo_internacional(self):
        ctx, cliente = self._ctx()
        ctx.args = ["71234567", "+51987654321"]
        ComandoVincular().ejecutar(ctx)
        self.assertTrue(AccesoTelegram.objects.filter(vehiculo=self.vehiculo, chat_id="111").exists())

    def test_vincular_datos_incorrectos(self):
        ctx, cliente = self._ctx()
        ctx.args = ["00000000", "999999999"]
        ComandoVincular().ejecutar(ctx)

        self.assertFalse(AccesoTelegram.objects.filter(chat_id="111").exists())
        self.assertTrue(any("intento 1/5" in m["texto"] for m in cliente.mensajes_enviados))

    def test_vincular_limite_de_intentos(self):
        for _ in range(5):
            ctx, _ = self._ctx()
            ctx.args = ["00000000", "999999999"]
            ComandoVincular().ejecutar(ctx)

        ctx, cliente = self._ctx()
        ctx.args = ["00000000", "999999999"]
        ComandoVincular().ejecutar(ctx)
        self.assertTrue(any("Demasiados intentos" in m["texto"] for m in cliente.mensajes_enviados))
        self.assertEqual(len(cliente.mensajes_enviados), 1)
