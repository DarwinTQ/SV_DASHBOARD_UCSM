from django.test import TestCase
from django.utils import timezone

from monitoreo.models import (
    AlertaTelegram, Conductor, DeteccionYolo, Dispositivo, Incidencia, MedicionTiempo, TipoEvento, Vehiculo,
)
from monitoreo.services.camara_service import ResultadoCaptura
from monitoreo.services.detector_yolo import CajaDeteccion, ResultadoDeteccion
from monitoreo.services.incidencia_service import IncidenciaService


class CamaraFalsa:
    def __init__(self, exito=True):
        self._exito = exito

    def capturar(self, con_flash=False):
        if not self._exito:
            return ResultadoCaptura(exito=False, error="simulado: sin cámara")
        return ResultadoCaptura(exito=True, jpg=b"contenido-jpeg-falso", ancho=640, alto=480, captura_ms=100.0)


class FabricaCamaraFalsa:
    def __init__(self, exito=True):
        self._exito = exito

    def para(self, codigo):
        return CamaraFalsa(self._exito)


class DetectorYoloFalso:
    def __init__(self, cajas=None):
        self._cajas = cajas or []

    def detectar(self, imagen):
        return ResultadoDeteccion(cajas=self._cajas, imagen_anotada_jpg=b"anotada-falsa", tiempo_inferencia_ms=42.0)


class DifusorAlertasFalso:
    def __init__(self, exitoso=True):
        self._exitoso = exitoso
        self.llamadas = []
        self.silenciosas = []
        self.evidencias = []

    def difundir(self, incidencia, resultado_yolo=None, silenciosa=False, evidencia=None):
        from monitoreo.services.difusor_alertas import ResultadoDifusion

        self.llamadas.append(incidencia.pk)
        self.silenciosas.append(silenciosa)
        self.evidencias.append(evidencia)
        alerta = AlertaTelegram.objects.create(
            incidencia=incidencia, conductor=incidencia.dispositivo.vehiculo.conductor,
            chat_id="12345", mensaje="mensaje de prueba", con_imagen=True,
            estado_envio="ENVIADO" if self._exitoso else "FALLIDO",
            fecha_envio=timezone.now() if self._exitoso else None, codigo_http=200,
        )
        return ResultadoDifusion(
            alertas=[alerta], t_primera_confirmacion=alerta.fecha_envio, exito=self._exitoso,
        )


class PipelineIncidenciaTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
            telegram_chat_id="12345",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO")
        TipoEvento.objects.create(codigo="VIBRACION", nombre="Golpe o vibración", nivel_riesgo="MEDIO")
        TipoEvento.objects.create(codigo="PERSONA", nombre="Persona detectada (YOLO)", nivel_riesgo="ALTO")

    def _evento(self, **overrides):
        base = {
            "codigo": "NODO-01", "evento_uid": "UID-1", "tipo": "VIBRACION",
            "ts_ms": int(timezone.now().timestamp() * 1000), "ts_fuente": "SERVIDOR",
            "valor": 0.82, "umbral": 0.5, "unidad": "g",
            "aceleracion_g": 1.31, "nivel_audio_db": 62.4,
            "gps": {"fix": True, "sats": 7, "lat": -16.398912, "lon": -71.536987},
            "t_recepcion_ms": int(timezone.now().timestamp() * 1000),
        }
        base.update(overrides)
        return base

    def test_pipeline_sin_persona_conserva_tipo_sensor(self):
        difusor_falso = DifusorAlertasFalso()
        servicio = IncidenciaService(
            difusor_alertas=difusor_falso,
            fabrica_camara=FabricaCamaraFalsa(exito=True),
            detector_yolo=DetectorYoloFalso(cajas=[]),
        )
        incidencia = servicio.procesar_evento_sensor(self._evento())

        self.assertIsNotNone(incidencia)
        self.assertEqual(incidencia.tipo.codigo, "VIBRACION")
        self.assertEqual(incidencia.estado, "NOTIFICADA")
        self.assertEqual(Incidencia.objects.count(), 1)
        self.assertEqual(DeteccionYolo.objects.count(), 0)
        self.assertEqual(MedicionTiempo.objects.filter(incidencia=incidencia).count(), 1)
        self.assertEqual(AlertaTelegram.objects.filter(incidencia=incidencia).count(), 1)
        self.assertEqual(difusor_falso.llamadas, [incidencia.pk])

    def test_pipeline_con_persona_cambia_tipo_a_persona(self):
        caja_persona = CajaDeteccion(clase="person", confianza=0.87, x1=10, y1=10, x2=100, y2=200)
        servicio = IncidenciaService(
            difusor_alertas=DifusorAlertasFalso(),
            fabrica_camara=FabricaCamaraFalsa(exito=True),
            detector_yolo=DetectorYoloFalso(cajas=[caja_persona]),
        )
        incidencia = servicio.procesar_evento_sensor(self._evento())

        self.assertEqual(incidencia.tipo.codigo, "PERSONA")
        self.assertIn("Disparado por sensor: VIBRACION", incidencia.observacion)
        self.assertEqual(DeteccionYolo.objects.filter(incidencia=incidencia).count(), 1)

    def test_pipeline_sin_camara_registra_incidencia_sin_imagen(self):
        servicio = IncidenciaService(
            difusor_alertas=DifusorAlertasFalso(),
            fabrica_camara=FabricaCamaraFalsa(exito=False),
            detector_yolo=DetectorYoloFalso(cajas=[]),
        )
        incidencia = servicio.procesar_evento_sensor(self._evento())

        self.assertFalse(incidencia.imagen)
        self.assertIn("No se pudo capturar imagen", incidencia.observacion)

    def test_pipeline_sin_reloj_sensor(self):
        servicio = IncidenciaService(
            difusor_alertas=DifusorAlertasFalso(),
            fabrica_camara=FabricaCamaraFalsa(exito=True),
            detector_yolo=DetectorYoloFalso(cajas=[]),
        )
        incidencia = servicio.procesar_evento_sensor(self._evento(ts_ms=None))

        self.assertIn("Sin reloj del sensor", incidencia.observacion)
        medicion = MedicionTiempo.objects.get(incidencia=incidencia)
        self.assertIsNone(medicion.latencia_red_ms)

    def test_telegram_fallido_no_marca_notificada(self):
        servicio = IncidenciaService(
            difusor_alertas=DifusorAlertasFalso(exitoso=False),
            fabrica_camara=FabricaCamaraFalsa(exito=True),
            detector_yolo=DetectorYoloFalso(cajas=[]),
        )
        incidencia = servicio.procesar_evento_sensor(self._evento())
        self.assertEqual(incidencia.estado, "PENDIENTE")
