"""Pruebas del ``PuenteMQTT`` con mensajes simulados (sección 6.2).

No hace falta un broker: se inyecta un ``ClienteMqttFalso`` y se llaman los
manejadores directamente, igual que haría paho al recibir un mensaje. Así se
cubre telemetría, GPS, evento (y su duplicado por QoS 1), el *Last Will* y la
sincronización de reloj.
"""
from django.test import TestCase
from django.utils import timezone

from config import config
from monitoreo.models import Conductor, Dispositivo, EstadoDispositivo, TipoEvento, Vehiculo
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.services.mqtt_service import MensajeMqtt
from monitoreo.workers.puente_mqtt import PuenteMQTT


class ClienteMqttFalso:
    """Registra lo publicado en lugar de hablar con el broker."""

    def __init__(self, conectado: bool = True):
        self.publicaciones = []
        self._conectado = conectado
        self.recibidos = 0
        self.publicados = 0
        self.reconexiones = 0
        self.invalidos = 0

    def conectar(self) -> bool:
        return True

    def desconectar(self) -> None:
        self._conectado = False

    def conectado(self) -> bool:
        return self._conectado

    def esperar_conexion(self, timeout: float = 5.0) -> bool:
        return self._conectado

    def publicar(self, codigo, sufijo, carga, qos=1, retener=False) -> bool:
        self.publicaciones.append({
            "codigo": codigo, "sufijo": sufijo, "carga": carga, "qos": qos, "retener": retener,
        })
        self.publicados += 1
        return True

    def publicadas(self, sufijo: str) -> list:
        return [p for p in self.publicaciones if p["sufijo"] == sufijo]


class BasePuenteTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris",
            anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(
            vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO",
        )
        # EstadoEnVivo es un singleton del proceso: se limpia ANTES de crear el
        # puente, porque el puente se queda con la instancia que haya entonces.
        EstadoEnVivo._instancia = None
        self.estado_en_vivo = EstadoEnVivo.instancia()
        self.cliente = ClienteMqttFalso()
        self.puente = PuenteMQTT(cliente=self.cliente)

    def tearDown(self):
        from monitoreo.services.mqtt_service import PublicadorConfiguracion

        EstadoEnVivo._instancia = None
        # El publicador también es singleton del proceso: hay que soltar el
        # cliente falso para no contaminar otras pruebas.
        PublicadorConfiguracion._instancia = None

    def _mensaje(self, sufijo: str, carga: dict, codigo: str = "NODO-01", retenido: bool = False):
        return MensajeMqtt(
            topico=config.mqtt.topico(codigo, sufijo), codigo=codigo, sufijo=sufijo,
            carga=carga, t_recepcion_ms=int(timezone.now().timestamp() * 1000), retenido=retenido,
        )

    def _enviar(self, sufijo: str, carga: dict, codigo: str = "NODO-01", retenido: bool = False):
        self.puente.procesar_mensaje(self._mensaje(sufijo, carga, codigo, retenido))


class SincronizacionRelojTests(BasePuenteTests):
    def test_sync_req_se_responde_de_inmediato(self):
        mensaje = self._mensaje("sync/req", {"id": 17, "t0": 123456})
        self.puente.procesar_mensaje(mensaje)

        respuestas = self.cliente.publicadas("sync/resp")
        self.assertEqual(len(respuestas), 1)
        carga = respuestas[0]["carga"]
        self.assertEqual(carga["id"], 17)
        self.assertEqual(carga["t0"], 123456)
        # El server_ts_ms es la marca tomada ANTES de parsear el JSON.
        self.assertEqual(carga["server_ts_ms"], mensaje.t_recepcion_ms)
        self.assertEqual(respuestas[0]["qos"], 0)
        self.assertFalse(respuestas[0]["retener"])

    def test_sync_req_invalido_no_responde(self):
        self._enviar("sync/req", {"no_es": "una peticion"})
        self.assertEqual(self.cliente.publicadas("sync/resp"), [])


class TelemetriaMqttTests(BasePuenteTests):
    def _telemetria(self, **extra):
        base = {
            "codigo": "NODO-01", "firmware": "3.0.0", "mac": "24:6F:28:AA:BB:CC",
            "ip": "10.144.55.50", "boot_id": 123456, "seq": 1, "uptime_ms": 1000,
            "ts_ms": int(timezone.now().timestamp() * 1000), "ts_fuente": "SERVIDOR",
            "armado": True, "modo": "ESTACIONADO", "modo_fuente": "INERCIAL", "contacto": None,
            "wifi": {"rssi_dbm": -55},
            "sistema": {"ram_libre_kb": 210, "temp_c": 31.5},
            "gps": {"fix": True, "sats": 8, "lat": -16.3989123, "lon": -71.5369871,
                    "vel_kmh": 0.0, "rumbo_deg": 118.5, "hdop": 0.9},
            "imu": {"a_max_g": 1.02, "vib_max_g": 0.01, "variacion_g": 0.004, "muestras": 500},
            "audio": {"spl_db": 48.2, "spl_pico_db": 71.0, "piso_baja_db": -55.0,
                      "piso_alta_db": -56.3, "fft_us": 1420, "muestras": 16000},
            "mqtt": {"reconexiones": 0, "publicados": 120, "descartados": 0, "rtt_sync_ms": 9},
        }
        base.update(extra)
        return base

    def test_telemetria_actualiza_dispositivo_y_estado_en_vivo(self):
        self._enviar("telemetria", self._telemetria())

        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.ip, "10.144.55.50")
        self.assertEqual(self.dispositivo.version_firmware, "3.0.0")
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)

        lectura = self.estado_en_vivo.obtener("NODO-01")
        self.assertIsNotNone(lectura)
        self.assertEqual(lectura.modo, "ESTACIONADO")
        self.assertEqual(lectura.modo_fuente, "INERCIAL")
        self.assertEqual(lectura.ultima_telemetria["audio"]["fft_us"], 1420)
        self.assertEqual(lectura.ultima_telemetria["mqtt"]["rtt_sync_ms"], 9)

    def test_telemetria_de_dispositivo_desconocido_se_descarta(self):
        self._enviar("telemetria", self._telemetria(codigo="NODO-99"), codigo="NODO-99")
        self.assertIsNone(self.estado_en_vivo.obtener("NODO-99"))

    def test_telemetria_invalida_no_rompe_el_puente(self):
        self._enviar("telemetria", {"codigo": "NODO-01", "gps": {"fix": True, "sats": 8, "lat": 999}})
        self.assertIsNone(self.estado_en_vivo.obtener("NODO-01"))


class GpsMqttTests(BasePuenteTests):
    def _gps(self, **extra):
        base = {
            "ts_ms": int(timezone.now().timestamp() * 1000), "fix": True,
            "lat": -16.3989123, "lon": -71.5369871, "alt_m": 2335.2,
            "vel_kmh": 32.4, "rumbo_deg": 118.5, "sats": 8, "hdop": 0.9,
            "edad_ms": 120, "modo": "EN_MARCHA",
        }
        base.update(extra)
        return base

    def test_gps_con_fix_entra_al_rastro(self):
        self._enviar("gps", self._gps())

        punto = self.estado_en_vivo.ultimo_punto_gps("NODO-01")
        self.assertIsNotNone(punto)
        self.assertAlmostEqual(punto.lat, -16.3989123, places=7)
        self.assertAlmostEqual(punto.lon, -71.5369871, places=7)
        self.assertEqual(punto.rumbo_deg, 118.5)
        self.assertEqual(len(self.estado_en_vivo.rastro("NODO-01")), 1)
        self.assertEqual(self.estado_en_vivo.modo("NODO-01"), "EN_MARCHA")

    def test_gps_a_5_hz_acumula_el_rastro(self):
        for i in range(10):
            self._enviar("gps", self._gps(lat=-16.3989123 + i * 1e-5))
        self.assertEqual(len(self.estado_en_vivo.rastro("NODO-01")), 10)

    def test_gps_sin_fix_no_entra_al_rastro(self):
        self._enviar("gps", {"ts_ms": 1, "fix": False, "sats": 3})
        self.assertIsNone(self.estado_en_vivo.ultimo_punto_gps("NODO-01"))
        self.assertEqual(self.estado_en_vivo.rastro("NODO-01"), [])

    def test_gps_descartado_por_hdop_no_entra_al_rastro(self):
        """El firmware manda fix=false con motivo="hdop" (sección 3.5)."""
        self._enviar("gps", {"ts_ms": 1, "fix": False, "sats": 7, "motivo": "hdop", "hdop": 7.2})
        self.assertIsNone(self.estado_en_vivo.ultimo_punto_gps("NODO-01"))

    def test_gps_con_fix_sin_coordenadas_es_invalido(self):
        self._enviar("gps", {"ts_ms": 1, "fix": True, "sats": 8})
        self.assertIsNone(self.estado_en_vivo.ultimo_punto_gps("NODO-01"))


class EventoMqttTests(BasePuenteTests):
    def setUp(self):
        super().setUp()
        TipoEvento.objects.create(codigo="ROTURA_VENTANA", nombre="Rotura de ventana", nivel_riesgo="ALTO")
        # El puente solo encola: el pipeline lo ejecuta ProcesadorEventos.
        from monitoreo.workers.procesador_eventos import ProcesadorEventos

        ProcesadorEventos._instancia = None
        self.procesador = ProcesadorEventos.instancia()

    def tearDown(self):
        from monitoreo.workers.procesador_eventos import ProcesadorEventos

        ProcesadorEventos._instancia = None
        super().tearDown()

    def _evento(self, uid: str = "ABCD1234-7"):
        return {
            "codigo": "NODO-01", "evento_uid": uid, "tipo": "ROTURA_VENTANA", "nivel": "ALERTA",
            "ts_ms": int(timezone.now().timestamp() * 1000), "ts_fuente": "SERVIDOR",
            "modo": "ESTACIONADO", "armado": True,
            "evidencia": {
                "e_baja_db": -38.2, "piso_baja_db": -55.0, "e_alta_db": -41.0,
                "piso_alta_db": -56.3, "planitud": 0.42, "duracion_ms": 208,
                "vib_g": 0.31, "delta_vib_ms": -40,
            },
            "aceleracion_g": 1.31, "nivel_audio_db": 104.7,
            "gps": {"fix": True, "sats": 8, "lat": -16.3989123, "lon": -71.5369871,
                    "vel_kmh": 0.0, "rumbo_deg": 0.0, "hdop": 0.9},
        }

    def test_evento_se_encola_con_su_evidencia(self):
        self._enviar("evento", self._evento())

        encolado = self.procesador._cola.get_nowait()
        self.assertEqual(encolado["tipo"], "ROTURA_VENTANA")
        self.assertEqual(encolado["nivel"], "ALERTA")
        self.assertEqual(encolado["origen"], "MQTT")
        self.assertEqual(encolado["evidencia"]["duracion_ms"], 208)
        self.assertEqual(encolado["evidencia"]["delta_vib_ms"], -40)
        self.assertIn("t_recepcion_ms", encolado)

    def test_evento_duplicado_se_descarta(self):
        """QoS 1 puede reentregar el mismo mensaje: manda el ``evento_uid``."""
        self._enviar("evento", self._evento(uid="MISMO-UID"))
        self._enviar("evento", self._evento(uid="MISMO-UID"))

        self.assertEqual(self.procesador._cola.qsize(), 1)
        self.assertEqual(self.puente._eventos_duplicados, 1)

    def test_evento_de_tipo_desconocido_se_descarta(self):
        evento = self._evento()
        evento["tipo"] = "NO_EXISTE"
        self._enviar("evento", evento)
        self.assertEqual(self.procesador._cola.qsize(), 0)


class EstadoYModoMqttTests(BasePuenteTests):
    def test_estado_en_linea_marca_contacto_y_publica_config(self):
        self._enviar("estado", {
            "en_linea": True, "codigo": "NODO-01", "firmware": "3.0.0",
            "ip": "10.144.55.50", "mac": "24:6F:28:AA:BB:CC", "rssi_dbm": -52,
            "boot_id": 999, "armado": True, "modo": "ESTACIONADO",
        }, retenido=True)

        self.dispositivo.refresh_from_db()
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)
        self.assertEqual(self.dispositivo.ip, "10.144.55.50")
        self.assertTrue(EstadoDispositivo.objects.filter(dispositivo=self.dispositivo, en_linea=True).exists())

        lectura = self.estado_en_vivo.obtener("NODO-01")
        self.assertTrue(lectura.en_linea_mqtt)

        # Al verlo aparecer se le reenvía su configuración vigente (retenida).
        configs = self.cliente.publicadas("config")
        self.assertEqual(len(configs), 1)
        self.assertTrue(configs[0]["retener"])
        self.assertTrue(configs[0]["carga"]["armado"])
        self.assertEqual(configs[0]["carga"]["umbrales"], config.deteccion.a_dict())

    def test_last_will_marca_fuera_de_linea_de_inmediato(self):
        """El *Last Will* del broker solo trae ``{"en_linea": false}``."""
        self._enviar("estado", {"en_linea": False})

        lectura = self.estado_en_vivo.obtener("NODO-01")
        self.assertFalse(lectura.en_linea_mqtt)
        self.assertTrue(EstadoDispositivo.objects.filter(dispositivo=self.dispositivo, en_linea=False).exists())
        # No se publica config a un nodo que acaba de caer.
        self.assertEqual(self.cliente.publicadas("config"), [])

    def test_modo_actualiza_estado_en_vivo(self):
        self._enviar("modo", {"modo": "EN_MARCHA", "fuente": "INERCIAL", "ts_ms": 1760330000113})

        self.assertEqual(self.estado_en_vivo.modo("NODO-01"), "EN_MARCHA")
        self.assertFalse(self.estado_en_vivo.esta_estacionado("NODO-01"))

        self._enviar("modo", {"modo": "ESTACIONADO", "fuente": "CONTACTO", "contacto": False})
        self.assertTrue(self.estado_en_vivo.esta_estacionado("NODO-01"))
        lectura = self.estado_en_vivo.obtener("NODO-01")
        self.assertEqual(lectura.modo_fuente, "CONTACTO")
        self.assertIs(lectura.contacto, False)

    def test_cam_estado_guarda_mac_y_estado(self):
        self._enviar("cam/estado", {
            "en_linea": True, "ok": True, "codigo": "CAM-NODO-01", "codigo_nodo": "NODO-01",
            "mac": "AA:BB:CC:DD:EE:FF", "ip": "10.144.55.120", "resolucion": "VGA",
            "calidad_jpeg": 12, "fps_max": 8, "fps_real": 7.4, "clientes_stream": 1,
        })

        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.mac_esp32cam, "AA:BB:CC:DD:EE:FF")
        lectura = self.estado_en_vivo.obtener("NODO-01")
        self.assertTrue(lectura.camara_estado["ok"])
        self.assertEqual(lectura.camara_estado["clientes_stream"], 1)

    def test_cam_estado_last_will_marca_camara_caida(self):
        self._enviar("cam/estado", {"en_linea": False})
        lectura = self.estado_en_vivo.obtener("NODO-01")
        self.assertFalse(lectura.camara_estado["ok"])


class DespachoTopicosTests(BasePuenteTests):
    def test_topico_sin_manejador_se_ignora(self):
        self._enviar("algo/raro", {"x": 1})     # no debe lanzar
        self.assertEqual(self.cliente.publicaciones, [])

    def test_un_manejador_que_falla_no_tumba_el_puente(self):
        """Cada ciclo está aislado: un error se registra y el puente sigue."""
        def explotar(_mensaje):
            raise RuntimeError("fallo simulado")

        self.puente._manejadores["telemetria"] = explotar
        self._enviar("telemetria", {"codigo": "NODO-01"})   # no debe propagar
        self._enviar("sync/req", {"id": 1, "t0": 2})
        self.assertEqual(len(self.cliente.publicadas("sync/resp")), 1)
