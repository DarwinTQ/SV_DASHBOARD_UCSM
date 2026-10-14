from django.test import TransactionTestCase
from django.utils import timezone

from monitoreo.models import AccesoTelegram, AlertaTelegram, Conductor, Dispositivo, Incidencia, TipoEvento, Vehiculo
from monitoreo.services.difusor_alertas import DifusorAlertas
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class DifusorAlertasTests(TransactionTestCase):
    # DifusorAlertas usa hilos reales (ThreadPoolExecutor); TestCase envuelve
    # cada prueba en una transacción que bloquearía esos hilos al escribir
    # ("database table is locked"). TransactionTestCase no lo hace.
    reset_sequences = False

    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=self.vehiculo, codigo="NODO-01")
        self.tipo = TipoEvento.objects.create(codigo="PERSONA", nombre="Persona detectada (YOLO)", nivel_riesgo="ALTO")
        self.incidencia = Incidencia.objects.create(dispositivo=self.dispositivo, tipo=self.tipo, fecha_hora=timezone.now())

        self.propietario = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Juan", rol="PROPIETARIO", chat_id="111",
            puede_controlar=True, recibe_alertas=True,
        )
        self.familiar_activo = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Maria", rol="FAMILIAR", chat_id="222", recibe_alertas=True,
        )
        self.familiar_sin_alertas = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Pedro", rol="FAMILIAR", chat_id="333", recibe_alertas=False,
        )
        self.familiar_silenciado = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Ana", rol="FAMILIAR", chat_id="444", recibe_alertas=True,
            silenciado_hasta=timezone.now() + timezone.timedelta(minutes=30),
        )

    def test_difunde_solo_a_destinatarios_validos(self):
        cliente_falso = ClienteBotAPIFalso()
        resultado = DifusorAlertas(cliente_bot=cliente_falso).difundir(self.incidencia)

        chats_notificados = {m["chat_id"] for m in cliente_falso.mensajes_enviados}
        chats_notificados |= {f["chat_id"] for f in cliente_falso.fotos_enviadas}

        self.assertIn("111", chats_notificados)
        self.assertIn("222", chats_notificados)
        self.assertNotIn("333", chats_notificados)
        self.assertNotIn("444", chats_notificados)

        self.assertEqual(AlertaTelegram.objects.filter(incidencia=self.incidencia).count(), 2)
        self.assertTrue(resultado.exito)
        self.assertIsNotNone(resultado.t_primera_confirmacion)
        self.assertTrue(all(a.fue_exitosa for a in resultado.alertas))

    def test_sin_destinatarios_no_falla(self):
        AccesoTelegram.objects.all().update(activo=False)
        cliente_falso = ClienteBotAPIFalso()
        resultado = DifusorAlertas(cliente_bot=cliente_falso).difundir(self.incidencia)

        self.assertFalse(resultado.exito)
        self.assertEqual(resultado.alertas, [])
        self.assertEqual(len(cliente_falso.mensajes_enviados), 0)
