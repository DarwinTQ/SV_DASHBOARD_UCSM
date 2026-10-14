from django.test import TestCase
from django.utils import timezone

from config import config
from monitoreo.bot.comandos.inicio import ComandoInicio
from monitoreo.bot.comandos.vinculacion import ComandoUnirse
from monitoreo.bot.contexto import ContextoComando
from monitoreo.bot.invitaciones import ServicioInvitaciones
from monitoreo.models import AccesoTelegram, Conductor, Vehiculo
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class InvitacionesTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321", telegram_chat_id="999",
        )
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres=conductor.nombre_completo, rol="PROPIETARIO",
            chat_id="999", puede_controlar=True, fecha_vinculacion=timezone.now(),
        )

    def _ctx(self, chat_id):
        cliente = ClienteBotAPIFalso()
        return ContextoComando(chat_id=chat_id, cliente=cliente), cliente

    def test_generar_invitacion_codigo_valido(self):
        acceso, error = ServicioInvitaciones().crear_invitacion(self.vehiculo, "Maria", "esposa")
        self.assertIsNone(error)
        self.assertEqual(len(acceso.codigo_invitacion), 6)
        self.assertFalse(any(c in acceso.codigo_invitacion for c in "0O1I"))

    def test_canjear_con_unirse(self):
        acceso, _ = ServicioInvitaciones().crear_invitacion(self.vehiculo, "Maria", "esposa")
        ctx, cliente = self._ctx("222")
        ctx.args = [acceso.codigo_invitacion]
        ComandoUnirse().ejecutar(ctx)

        acceso.refresh_from_db()
        self.assertTrue(acceso.vinculado)
        self.assertEqual(acceso.chat_id, "222")
        self.assertTrue(any(m["chat_id"] == "999" for m in cliente.mensajes_enviados))

    def test_canjear_con_start_codigo(self):
        acceso, _ = ServicioInvitaciones().crear_invitacion(self.vehiculo, "Pedro", "hijo")
        ctx, cliente = self._ctx("333")
        ctx.args = [acceso.codigo_invitacion]
        ComandoInicio().ejecutar(ctx)

        acceso.refresh_from_db()
        self.assertTrue(acceso.vinculado)
        self.assertEqual(acceso.chat_id, "333")

    def test_codigo_vencido(self):
        acceso, _ = ServicioInvitaciones().crear_invitacion(self.vehiculo, "Ana")
        acceso.invitacion_expira = timezone.now() - timezone.timedelta(hours=1)
        acceso.save()

        ctx, cliente = self._ctx("444")
        ctx.args = [acceso.codigo_invitacion]
        ComandoUnirse().ejecutar(ctx)

        acceso.refresh_from_db()
        self.assertFalse(acceso.vinculado)
        self.assertTrue(any("venci" in m["texto"] for m in cliente.mensajes_enviados))

    def test_codigo_reutilizado(self):
        acceso, _ = ServicioInvitaciones().crear_invitacion(self.vehiculo, "Luis")
        codigo = acceso.codigo_invitacion
        ServicioInvitaciones().canjear(codigo, "555", "luis_tg")

        ctx, cliente = self._ctx("666")
        ctx.args = [codigo]
        ComandoUnirse().ejecutar(ctx)
        self.assertTrue(any("válido" in m["texto"] or "utilizado" in m["texto"] for m in cliente.mensajes_enviados))

    def test_maximo_accesos_por_vehiculo(self):
        for i in range(config.telegram.max_accesos_por_vehiculo - 1):  # ya hay 1 (propietario)
            _, error = ServicioInvitaciones().crear_invitacion(self.vehiculo, f"Familiar {i}")
            self.assertIsNone(error)

        _, error = ServicioInvitaciones().crear_invitacion(self.vehiculo, "Excedente")
        self.assertIsNotNone(error)
