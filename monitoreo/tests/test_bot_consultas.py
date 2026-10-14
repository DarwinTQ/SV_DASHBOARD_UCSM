from unittest.mock import patch

from django.test import TestCase

from monitoreo.bot.comandos.estado import ComandoEstado
from monitoreo.bot.comandos.foto import ComandoFoto
from monitoreo.bot.contexto import ContextoComando
from monitoreo.models import AccesoTelegram, Conductor, Dispositivo, Vehiculo
from monitoreo.services.camara_service import ResultadoCaptura
from monitoreo.services.detector_yolo import ResultadoDeteccion
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso


class ComandoEstadoTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=self.vehiculo, codigo="NODO-01", estado="ACTIVO")
        self.acceso = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Juan", rol="PROPIETARIO", chat_id="111", puede_controlar=True,
        )

        EstadoEnVivo.instancia().actualizar_telemetria("NODO-01", {
            "armado": True, "bateria": {"v": 3.9, "pct": 80}, "wifi": {"rssi_dbm": -55},
            "sistema": {"temp_c": 30}, "gps": {"fix": True, "lat": -16.4, "lon": -71.5, "vel_kmh": 10},
        })

    def test_estado_responde_con_datos_en_vivo(self):
        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso])
        ComandoEstado().ejecutar(ctx)

        self.assertEqual(len(cliente.mensajes_enviados), 1)
        texto = cliente.mensajes_enviados[0]["texto"]
        self.assertIn("V1A-234", texto)
        self.assertIn("Armado", texto)

    def _crear_segundo_vehiculo(self):
        conductor2 = Conductor.objects.create(dni="70000000", nombres="Pedro", apellidos="Gómez", telefono="+51900000000")
        vehiculo2 = Vehiculo.objects.create(
            conductor=conductor2, placa="XYZ-999", marca="Kia", modelo="Rio", anio=2019, color="Negro",
        )
        Dispositivo.objects.create(vehiculo=vehiculo2, codigo="NODO-02", estado="ACTIVO")
        return AccesoTelegram.objects.create(
            vehiculo=vehiculo2, nombres="Juan", rol="PROPIETARIO", chat_id="111", puede_controlar=True,
        )

    def test_seleccion_por_placa_cuando_hay_varios_vehiculos(self):
        acceso2 = self._crear_segundo_vehiculo()

        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso, acceso2])
        ctx.args = ["V1A-234"]
        ComandoEstado().ejecutar(ctx)

        self.assertEqual(len(cliente.mensajes_enviados), 1)
        self.assertIn("V1A-234", cliente.mensajes_enviados[0]["texto"])

    def test_sin_placa_y_varios_vehiculos_pide_elegir(self):
        acceso2 = self._crear_segundo_vehiculo()

        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso, acceso2])
        ComandoEstado().ejecutar(ctx)

        self.assertEqual(len(cliente.mensajes_enviados), 1)
        self.assertIsNotNone(cliente.mensajes_enviados[0]["reply_markup"])


class ComandoFotoTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        self.vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=self.vehiculo, codigo="NODO-01", estado="ACTIVO")
        self.acceso = AccesoTelegram.objects.create(
            vehiculo=self.vehiculo, nombres="Juan", rol="PROPIETARIO", chat_id="111", puede_controlar=True,
        )

    @patch("monitoreo.services.detector_yolo.DetectorYOLO.instancia")
    @patch("monitoreo.services.camara_service.FabricaCamara.para")
    def test_foto_responde_con_imagen_y_pie_de_foto(self, camara_para, detector_instancia):
        camara_para.return_value.capturar.return_value = ResultadoCaptura(exito=True, jpg=b"jpeg-falso", captura_ms=90.0)
        detector_instancia.return_value.detectar.return_value = ResultadoDeteccion(
            cajas=[], imagen_anotada_jpg=None, tiempo_inferencia_ms=12.0,
        )

        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso])
        ComandoFoto().ejecutar(ctx)

        self.assertEqual(len(cliente.fotos_enviadas), 1)
        self.assertIn("NODO-01", cliente.fotos_enviadas[0]["caption"])

    @patch("monitoreo.services.camara_service.FabricaCamara.para")
    def test_foto_sin_camara_y_sin_frame_previo_responde_error(self, camara_para):
        camara_para.return_value.capturar.return_value = ResultadoCaptura(exito=False, error="timeout")

        cliente = ClienteBotAPIFalso()
        ctx = ContextoComando(chat_id="111", cliente=cliente, accesos=[self.acceso])
        ComandoFoto().ejecutar(ctx)

        self.assertEqual(len(cliente.fotos_enviadas), 0)
        self.assertTrue(any("No se pudo capturar" in m["texto"] for m in cliente.mensajes_enviados))
