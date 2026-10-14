from django.test import TestCase
from django.utils import timezone

from monitoreo.bot.comandos.registro import RegistroComandos
from monitoreo.bot.contexto import ContextoComando
from monitoreo.bot.manejador_callbacks import ManejadorCallbacks
from monitoreo.models import AccesoTelegram, AlertaTelegram, Conductor, Dispositivo, Incidencia, TipoEvento, Vehiculo
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class ValidacionCompartidaTests(TestCase):
    """Sección 7: el primero que valida gana, y se editan los botones de
    TODOS los chats que recibieron la alerta."""

    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=self.vehiculo, codigo="NODO-01")
        self.tipo = TipoEvento.objects.create(codigo="PERSONA", nombre="Persona detectada (YOLO)", nivel_riesgo="ALTO")
        self.incidencia = Incidencia.objects.create(dispositivo=self.dispositivo, tipo=self.tipo, fecha_hora=timezone.now())

        self.propietario = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Juan", rol="PROPIETARIO", chat_id="111", puede_controlar=True,
        )
        self.familiar = AccesoTelegram.objects.create(vehiculo=self.vehiculo, nombres="Maria", rol="FAMILIAR", chat_id="222")

        AlertaTelegram.objects.create(
            incidencia=self.incidencia, conductor=conductor, chat_id="111", mensaje="x",
            message_id=5001, estado_envio="ENVIADO",
        )
        AlertaTelegram.objects.create(
            incidencia=self.incidencia, conductor=conductor, chat_id="222", mensaje="x",
            message_id=5002, estado_envio="ENVIADO",
        )

        self.manejador = ManejadorCallbacks(RegistroComandos())

    def _ctx(self, chat_id, accesos):
        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id=chat_id, cliente=cliente, accesos=accesos, es_callback=True, callback_query_id="cb1")
        return ctx, cliente

    def test_primero_en_validar_gana_y_edita_todos_los_mensajes(self):
        ctx, cliente = self._ctx("111", [self.propietario])
        self.manejador.procesar(ctx, f"v:{self.incidencia.pk}:VP")

        self.incidencia.refresh_from_db()
        self.assertEqual(self.incidencia.validacion, "VP")
        self.assertEqual(self.incidencia.estado, "ATENDIDA")

        chats_editados = {m["chat_id"] for m in cliente.markups_editados}
        self.assertEqual(chats_editados, {"111", "222"})
        self.assertTrue(any(
            "Juan" in m["reply_markup"]["inline_keyboard"][0][0]["text"] for m in cliente.markups_editados
        ))

    def test_segundo_en_validar_recibe_aviso(self):
        ctx1, _ = self._ctx("111", [self.propietario])
        self.manejador.procesar(ctx1, f"v:{self.incidencia.pk}:VP")

        ctx2, cliente2 = self._ctx("222", [self.familiar])
        self.manejador.procesar(ctx2, f"v:{self.incidencia.pk}:FP")

        self.incidencia.refresh_from_db()
        self.assertEqual(self.incidencia.validacion, "VP")
        self.assertTrue(any("ya fue validada" in c["texto"] for c in cliente2.callbacks_respondidos))

    def test_sin_acceso_no_puede_validar(self):
        ctx, cliente = self._ctx("999", [])
        self.manejador.procesar(ctx, f"v:{self.incidencia.pk}:VP")

        self.incidencia.refresh_from_db()
        self.assertIsNone(self.incidencia.validacion)
        self.assertTrue(any("no tienes acceso" in c["texto"].lower() for c in cliente.callbacks_respondidos))
