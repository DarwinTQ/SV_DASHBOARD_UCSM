from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from monitoreo.models import Conductor, Dispositivo, Incidencia, MedicionTiempo, TipoEvento, Vehiculo


class MedicionTiempoTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        dispositivo = Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01")
        tipo = TipoEvento.objects.create(codigo="VIBRACION", nombre="Golpe o vibración", nivel_riesgo="MEDIO")
        self.incidencia = Incidencia.objects.create(
            dispositivo=dispositivo, tipo=tipo, fecha_hora=timezone.now(),
        )

    def test_calcula_todos_los_tiempos(self):
        base = timezone.now()
        medicion = MedicionTiempo(
            incidencia=self.incidencia,
            t_deteccion_sensor=base,
            t_recepcion_servidor=base + timedelta(milliseconds=50),
            t_inicio_inferencia=base + timedelta(milliseconds=60),
            t_fin_inferencia=base + timedelta(milliseconds=160),
            t_registro_bd=base + timedelta(milliseconds=180),
            t_envio_telegram=base + timedelta(milliseconds=190),
            t_confirmacion_telegram=base + timedelta(milliseconds=390),
        )
        medicion.save()

        self.assertAlmostEqual(medicion.latencia_red_ms, 50, delta=0.5)
        self.assertAlmostEqual(medicion.tiempo_inferencia_ms, 100, delta=0.5)
        self.assertAlmostEqual(medicion.tiempo_bd_ms, 20, delta=0.5)
        self.assertAlmostEqual(medicion.latencia_telegram_ms, 200, delta=0.5)
        self.assertAlmostEqual(medicion.tiempo_respuesta_total_ms, 390, delta=0.5)
        self.assertAlmostEqual(medicion.fps, 10, delta=0.1)

    def test_sin_reloj_sensor_usa_recepcion_servidor(self):
        base = timezone.now()
        medicion = MedicionTiempo(
            incidencia=self.incidencia,
            t_deteccion_sensor=base,
            t_recepcion_servidor=base,
            t_confirmacion_telegram=base + timedelta(milliseconds=300),
        )
        medicion._sin_reloj_sensor = True
        medicion.save()

        self.assertIsNone(medicion.latencia_red_ms)
        self.assertAlmostEqual(medicion.tiempo_respuesta_total_ms, 300, delta=0.5)

    def test_sin_mediciones_de_inferencia_fps_es_none(self):
        base = timezone.now()
        medicion = MedicionTiempo(
            incidencia=self.incidencia, t_deteccion_sensor=base, t_recepcion_servidor=base,
        )
        medicion.save()
        self.assertIsNone(medicion.fps)
        self.assertIsNone(medicion.tiempo_inferencia_ms)
