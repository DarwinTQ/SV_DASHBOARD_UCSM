from django.test import TestCase
from django.utils import timezone

from monitoreo.bot.comandos.alertas import ComandoSilenciar
from monitoreo.bot.comandos.ubicacion import ComandoUbicacion
from monitoreo.bot.contexto import ContextoComando
from monitoreo.models import AccesoTelegram, Conductor, Dispositivo, Vehiculo
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class ComandoUbicacionTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=self.vehiculo, codigo="NODO-01", estado="ACTIVO")
        self.acceso = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Juan", rol="PROPIETARIO", chat_id="111", puede_controlar=True,
        )
        EstadoEnVivo._instancia = None  # es un singleton en memoria: aislar cada prueba

    def test_ubicacion_con_gps_valido_envia_location(self):
        EstadoEnVivo.instancia().actualizar_telemetria("NODO-01", {
            "gps": {"fix": True, "lat": -16.4, "lon": -71.5, "vel_kmh": 12, "sats": 8},
        })
        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso])
        ComandoUbicacion().ejecutar(ctx)

        self.assertEqual(len(cliente.ubicaciones_enviadas), 1)
        self.assertEqual(cliente.ubicaciones_enviadas[0]["lat"], -16.4)
        self.assertTrue(any("V1A-234" in m["texto"] for m in cliente.mensajes_enviados))

    def test_ubicacion_sin_gps_responde_mensaje_claro(self):
        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso])
        ComandoUbicacion().ejecutar(ctx)

        self.assertEqual(len(cliente.ubicaciones_enviadas), 0)
        self.assertTrue(any("no hay" in m["texto"].lower() for m in cliente.mensajes_enviados))


class ComandoSilenciarTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.acceso = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Maria", rol="FAMILIAR", chat_id="222", recibe_alertas=True,
        )

    def test_silenciar_30_minutos(self):
        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="222", cliente=cliente, accesos=[self.acceso])
        ctx.args = ["30"]
        ComandoSilenciar().ejecutar(ctx)

        self.acceso.refresh_from_db()
        self.assertIsNotNone(self.acceso.silenciado_hasta)
        self.assertGreater(self.acceso.silenciado_hasta, timezone.now())
        self.assertTrue(any("30 min" in m["texto"] for m in cliente.mensajes_enviados))

    def test_silenciar_0_reanuda(self):
        self.acceso.silenciado_hasta = timezone.now() + timezone.timedelta(hours=1)
        self.acceso.save()

        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="222", cliente=cliente, accesos=[self.acceso])
        ctx.args = ["0"]
        ComandoSilenciar().ejecutar(ctx)

        self.acceso.refresh_from_db()
        self.assertIsNone(self.acceso.silenciado_hasta)
