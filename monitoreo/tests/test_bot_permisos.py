from django.test import TestCase

from monitoreo.bot.comandos.control import ComandoArmar
from monitoreo.bot.comandos.familia import ComandoInvitar
from monitoreo.bot.contexto import ContextoComando
from monitoreo.models import AccesoTelegram, Conductor, Dispositivo, Vehiculo
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class PermisosTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=self.vehiculo, codigo="NODO-01", estado="INACTIVO")
        self.familiar_sin_control = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Maria", rol="FAMILIAR", chat_id="222", puede_controlar=False,
        )
        self.familiar_con_control = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Pedro", rol="FAMILIAR", chat_id="333", puede_controlar=True,
        )

    def _ctx(self, chat_id, accesos):
        cliente = ClienteBotAPIFalso()
        return ContextoComando(chat_id=chat_id, cliente=cliente, accesos=accesos), cliente

    def test_familiar_sin_control_no_puede_armar(self):
        ctx, cliente = self._ctx("222", [self.familiar_sin_control])
        ComandoArmar().ejecutar(ctx)

        self.assertTrue(any("permiso" in m["texto"] for m in cliente.mensajes_enviados))
        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.estado, "INACTIVO")

    def test_familiar_con_control_puede_iniciar_armado(self):
        ctx, cliente = self._ctx("333", [self.familiar_con_control])
        ComandoArmar().ejecutar(ctx)

        self.assertTrue(any("Confirmas ARMAR" in m["texto"] for m in cliente.mensajes_enviados))

    def test_familiar_no_puede_invitar(self):
        ctx, cliente = self._ctx("222", [self.familiar_sin_control])
        ctx.args = ["Carlos"]
        ComandoInvitar().ejecutar(ctx)

        self.assertTrue(any("permiso" in m["texto"] for m in cliente.mensajes_enviados))
        self.assertFalse(AccesoTelegram.objects.filter(nombres="Carlos").exists())
