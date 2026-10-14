from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from monitoreo.models import (
    AlertaTelegram, Conductor, Dispositivo, EstadoDispositivo, Incidencia, MedicionTiempo, TipoEvento, Vehiculo,
)
from monitoreo.services.disponibilidad_service import DisponibilidadService
from monitoreo.services.metricas_service import MetricasService


class MetricasServiceTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        vehiculo = Vehiculo.objects.create(conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata")
        self.dispositivo = Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01")
        self.tipo = TipoEvento.objects.create(codigo="VIBRACION", nombre="Golpe o vibración", nivel_riesgo="MEDIO")
        self.servicio = MetricasService()

    def _crear_incidencia(self, tiempo_total_ms, validacion=None):
        base = timezone.now()
        incidencia = Incidencia.objects.create(dispositivo=self.dispositivo, tipo=self.tipo, fecha_hora=base, validacion=validacion)
        medicion = MedicionTiempo(
            incidencia=incidencia, t_deteccion_sensor=base, t_recepcion_servidor=base,
            t_confirmacion_telegram=base + timedelta(milliseconds=tiempo_total_ms),
        )
        medicion.save()
        return incidencia

    def test_estadisticas_tiempos_basicas(self):
        for ms in [100, 200, 300, 400, 500]:
            self._crear_incidencia(ms)

        est = self.servicio.estadisticas_tiempos()["tiempo_respuesta_total_ms"]
        self.assertEqual(est["n"], 5)
        self.assertAlmostEqual(est["media"], 300, delta=1)
        self.assertAlmostEqual(est["minimo"], 100, delta=1)
        self.assertAlmostEqual(est["maximo"], 500, delta=1)
        self.assertAlmostEqual(est["p50"], 300, delta=1)

    def test_estadisticas_sin_datos_devuelve_none(self):
        est = self.servicio.estadisticas_tiempos()["tiempo_respuesta_total_ms"]
        self.assertEqual(est["n"], 0)
        self.assertIsNone(est["media"])

    def test_matriz_confusion(self):
        self._crear_incidencia(100, validacion="VP")
        self._crear_incidencia(100, validacion="VP")
        self._crear_incidencia(100, validacion="FP")
        self._crear_incidencia(100, validacion="FN")
        self._crear_incidencia(100, validacion="VN")
        self._crear_incidencia(100, validacion=None)

        matriz = self.servicio.matriz_confusion()
        self.assertEqual(matriz["conteo"], {"VP": 2, "FP": 1, "FN": 1, "VN": 1})
        self.assertAlmostEqual(matriz["precision"], 2 / 3, places=3)
        self.assertAlmostEqual(matriz["exhaustividad"], 2 / 3, places=3)
        self.assertEqual(matriz["sin_validar"], 1)
        self.assertEqual(matriz["total"], 6)

    def test_disponibilidad(self):
        for _ in range(3):
            EstadoDispositivo.objects.create(dispositivo=self.dispositivo, en_linea=True)
        for _ in range(1):
            EstadoDispositivo.objects.create(dispositivo=self.dispositivo, en_linea=False)

        resultado = DisponibilidadService().calcular()
        self.assertEqual(resultado["total_muestras"], 4)
        self.assertEqual(resultado["disponibilidad_pct"], 75.0)

    def test_resumen_alertas(self):
        incidencia = self._crear_incidencia(100)
        conductor = incidencia.dispositivo.vehiculo.conductor
        AlertaTelegram.objects.create(incidencia=incidencia, conductor=conductor, chat_id="1", mensaje="x", estado_envio="ENVIADO", intentos=1)
        AlertaTelegram.objects.create(incidencia=incidencia, conductor=conductor, chat_id="1", mensaje="x", estado_envio="FALLIDO", intentos=3)

        resumen = self.servicio.resumen_alertas()
        self.assertEqual(resumen["total"], 2)
        self.assertEqual(resumen["enviadas"], 1)
        self.assertEqual(resumen["fallidas"], 1)
        self.assertEqual(resumen["tasa_exito"], 50.0)
