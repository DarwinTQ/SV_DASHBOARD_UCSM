"""Comando: python manage.py simular_nodo --codigo NODO-01 --segundos 120 --eventos 5

Emula el nodo ESP32-S3 v3.0.0 **por MQTT**, con el contrato de la sección 2:

    ucsm/v1/<codigo>/estado      al conectar (retenido) + Last Will
    ucsm/v1/<codigo>/modo        al cambiar (retenido)
    ucsm/v1/<codigo>/telemetria  cada 1 s
    ucsm/v1/<codigo>/gps         5 Hz EN_MARCHA / 1 Hz ESTACIONADO
    ucsm/v1/<codigo>/evento      QoS 1, con evento_uid
    ucsm/v1/<codigo>/sync/req    cada 30 s (y 5 veces al conectar)

Hace un recorrido real por Arequipa a 5 Hz (avenida Ejército → Yanahuara) con
rumbo y velocidad coherentes, y dispara los cinco tipos de evento de las reglas
definitivas con su evidencia completa.

Con ``--http`` usa el camino de respaldo (``POST /api/v1/...``) en vez de MQTT,
para comprobar que los dos caminos siguen funcionando.
"""
from __future__ import annotations

import json
import math
import random
import time
import uuid

from django.core.management.base import BaseCommand, CommandError

from config import config

# Recorrido por Arequipa: puntos de paso de la ruta simulada.
RUTA_AREQUIPA = [
    (-16.398866, -71.536961),   # Plaza de Armas
    (-16.396500, -71.540200),   # Av. Ejército
    (-16.393800, -71.544100),   # Puente Grau
    (-16.391200, -71.548500),   # Yanahuara
    (-16.388900, -71.552000),   # Mirador de Yanahuara
]

# Los cinco eventos de las reglas definitivas (sección 3.4), con su evidencia.
GUION_EVENTOS = [
    {
        "tipo": "CHOQUE", "nivel": "ALERTA", "modo": "EN_MARCHA",
        "evidencia": {"a_pico_g": 6.8, "spl_pico_db": 115.2, "delta_ms": 42},
        "aceleracion_g": 6.8, "nivel_audio_db": 115.2,
    },
    {
        "tipo": "IMPACTO_SIN_CONFIRMAR", "nivel": "REGISTRO", "modo": "EN_MARCHA",
        "evidencia": {"a_pico_g": 4.6, "spl_pico_db": 0.0, "delta_ms": None},
        "aceleracion_g": 4.6, "nivel_audio_db": 71.3,
    },
    {
        "tipo": "ROTURA_VENTANA", "nivel": "ALERTA", "modo": "ESTACIONADO",
        "evidencia": {
            "e_baja_db": -38.2, "piso_baja_db": -55.0, "e_alta_db": -41.0,
            "piso_alta_db": -56.3, "planitud": 0.42, "duracion_ms": 208,
            "vib_g": 0.31, "delta_vib_ms": -40,
        },
        "aceleracion_g": 0.31, "nivel_audio_db": 104.7,
    },
    {
        "tipo": "VIDRIO_ROTO_EXTERNO", "nivel": "REGISTRO", "modo": "ESTACIONADO",
        "evidencia": {
            "e_baja_db": -40.1, "piso_baja_db": -55.0, "e_alta_db": -43.5,
            "piso_alta_db": -56.3, "planitud": 0.38, "duracion_ms": 176,
            "vib_g": 0.0, "delta_vib_ms": None,
        },
        "aceleracion_g": 0.0, "nivel_audio_db": 98.2,
    },
    {
        "tipo": "GOLPE_MANIPULACION", "nivel": "ALERTA_LEVE", "modo": "ESTACIONADO",
        "evidencia": {
            "e_baja_db": -52.0, "piso_baja_db": -55.0, "e_alta_db": -56.0,
            "piso_alta_db": -56.3, "planitud": 0.0, "duracion_ms": 0,
            "vib_g": 0.42, "delta_vib_ms": 0,
        },
        "aceleracion_g": 0.42, "nivel_audio_db": 68.9,
    },
]


class RecorridoSimulado:
    """Interpola la ruta de Arequipa y calcula velocidad y rumbo coherentes."""

    def __init__(self, puntos=None, velocidad_kmh: float = 32.0):
        self._puntos = puntos or RUTA_AREQUIPA
        self._velocidad_kmh = velocidad_kmh
        self._avance = 0.0          # 0..len(puntos)-1
        self._lat, self._lon = self._puntos[0]
        self._rumbo = 0.0

    @staticmethod
    def _rumbo_entre(lat1, lon1, lat2, lon2) -> float:
        """Rumbo inicial en grados (0 = norte), como ``gps.course.deg()``."""
        dlon = math.radians(lon2 - lon1)
        lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
        y = math.sin(dlon) * math.cos(lat2_r)
        x = math.cos(lat1_r) * math.sin(lat2_r) - math.sin(lat1_r) * math.cos(lat2_r) * math.cos(dlon)
        return (math.degrees(math.atan2(y, x)) + 360) % 360

    def avanzar(self, dt_s: float, en_marcha: bool) -> None:
        if not en_marcha:
            self._velocidad_actual = 0.0
            return
        # Un tramo de la ruta se recorre en ~12 s a velocidad de ciudad.
        self._avance = (self._avance + dt_s / 12.0) % (len(self._puntos) - 1)
        indice = int(self._avance)
        fraccion = self._avance - indice
        lat1, lon1 = self._puntos[indice]
        lat2, lon2 = self._puntos[indice + 1]
        self._lat = lat1 + (lat2 - lat1) * fraccion
        self._lon = lon1 + (lon2 - lon1) * fraccion
        self._rumbo = self._rumbo_entre(lat1, lon1, lat2, lon2)
        self._velocidad_actual = self._velocidad_kmh + random.uniform(-4, 4)

    _velocidad_actual = 0.0

    def gps(self, ts_ms: int, modo: str) -> dict:
        en_marcha = modo == "EN_MARCHA"
        return {
            "ts_ms": ts_ms, "fix": True,
            "lat": round(self._lat + random.uniform(-2e-6, 2e-6), 7),
            "lon": round(self._lon + random.uniform(-2e-6, 2e-6), 7),
            "alt_m": round(2335.0 + random.uniform(-3, 3), 1),
            "vel_kmh": round(max(0.0, self._velocidad_actual) if en_marcha else 0.0, 1),
            "rumbo_deg": round(self._rumbo, 1),
            "sats": random.randint(6, 11),
            "hdop": round(random.uniform(0.8, 1.6), 1),
            "edad_ms": random.randint(40, 220),
            "modo": modo,
        }

    def gps_bloque(self) -> dict:
        """Bloque ``gps`` anidado en telemetría y eventos."""
        return {
            "fix": True, "sats": random.randint(6, 11),
            "lat": round(self._lat, 7), "lon": round(self._lon, 7),
            "vel_kmh": round(max(0.0, self._velocidad_actual), 1),
            "rumbo_deg": round(self._rumbo, 1),
            "alt_m": 2335.0, "hdop": round(random.uniform(0.8, 1.6), 1),
        }


class SimuladorNodo:
    """Emula el tráfico MQTT (o HTTP de respaldo) de un ESP32-S3 real."""

    def __init__(self, codigo: str, escribir, usar_http: bool = False, url_base: str = ""):
        self.codigo = codigo
        self._escribir = escribir
        self._usar_http = usar_http
        self._url_base = url_base.rstrip("/")
        self.boot_id = random.randint(10**9, 10**10 - 1)
        self.seq = 0
        self.num_evento = 0
        self.offset_reloj_ms = 0
        self.modo = "EN_MARCHA"
        self.recorrido = RecorridoSimulado()
        self.publicados = 0
        self._cliente = None
        self._sesion = None
        self._syncs_pendientes = 0

    # ------------------------------------------------------------------ #
    # Conexión
    # ------------------------------------------------------------------ #
    def conectar(self) -> None:
        if self._usar_http:
            import requests

            self._sesion = requests.Session()
            self._escribir(f"Simulando '{self.codigo}' por HTTP contra {self._url_base}")
            return

        import paho.mqtt.client as mqtt

        self._cliente = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"nodo-{self.codigo}-sim", clean_session=True,
        )
        if config.mqtt.usuario:
            self._cliente.username_pw_set(config.mqtt.usuario, config.mqtt.clave)
        # Mismo Last Will que el firmware: al cortar, el servidor lo nota solo.
        self._cliente.will_set(
            config.mqtt.topico(self.codigo, "estado"), json.dumps({"en_linea": False}),
            qos=1, retain=True,
        )
        self._cliente.on_message = self._al_recibir
        self._cliente.connect(config.mqtt.host, config.mqtt.puerto, config.mqtt.keepalive)
        self._cliente.subscribe(config.mqtt.topico(self.codigo, "sync/resp"), qos=0)
        self._cliente.subscribe(config.mqtt.topico(self.codigo, "config"), qos=1)
        self._cliente.subscribe(config.mqtt.topico(self.codigo, "cmd"), qos=1)
        self._cliente.loop_start()
        self._escribir(
            f"Simulando '{self.codigo}' por MQTT contra "
            f"{config.mqtt.host}:{config.mqtt.puerto} (prefijo {config.mqtt.prefijo})"
        )
        self.publicar_estado(True)
        self.publicar_modo()
        self._syncs_pendientes = 5   # 5 sincronizaciones seguidas al conectar

    def desconectar(self) -> None:
        if self._cliente is not None:
            self.publicar_estado(False)
            time.sleep(0.2)
            self._cliente.loop_stop()
            self._cliente.disconnect()
        if self._sesion is not None:
            self._sesion.close()

    # ------------------------------------------------------------------ #
    # Publicación
    # ------------------------------------------------------------------ #
    def _publicar(self, sufijo: str, carga: dict, qos: int = 0, retener: bool = False) -> None:
        if self._usar_http:
            self._publicar_http(sufijo, carga)
            return
        self._cliente.publish(
            config.mqtt.topico(self.codigo, sufijo), json.dumps(carga, ensure_ascii=False),
            qos=qos, retain=retener,
        )
        self.publicados += 1

    def _publicar_http(self, sufijo: str, carga: dict) -> None:
        """Camino de respaldo: solo telemetría y eventos tienen endpoint HTTP."""
        import requests

        rutas = {"telemetria": config.esp32.ruta_telemetria, "evento": config.esp32.ruta_eventos}
        ruta = rutas.get(sufijo)
        if ruta is None:
            return
        try:
            resp = self._sesion.post(
                f"{self._url_base}{ruta}", json=carga, timeout=5,
                headers={"X-API-Key": config.esp32.api_key, "X-Device-Code": self.codigo},
            )
            self.publicados += 1
            if resp.ok:
                server_ts = resp.json().get("server_ts_ms")
                if server_ts is not None:
                    self.offset_reloj_ms = server_ts - int(time.time() * 1000)
        except requests.RequestException as exc:
            self._escribir(f"  error de red en {sufijo}: {exc}")

    def _al_recibir(self, cliente, userdata, mensaje):
        sufijo = config.mqtt.sufijo_de_topico(mensaje.topic)
        try:
            carga = json.loads(mensaje.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return

        if sufijo == "sync/resp":
            # Mismo cálculo que RelojSincronizado del firmware.
            t1 = int(time.time() * 1000)
            t0 = carga.get("t0", t1)
            servidor = carga.get("server_ts_ms")
            if servidor:
                self.offset_reloj_ms = servidor - (t0 + t1) // 2
                self._escribir(f"  sync: rtt={t1 - t0} ms, offset={self.offset_reloj_ms} ms")
        elif sufijo == "config":
            self._escribir(f"  config recibida: armado={carga.get('armado')}")
        elif sufijo == "cmd":
            self._escribir(f"  cmd recibido: {carga}")
            if carga.get("accion") == "forzar_modo" and carga.get("modo"):
                self.modo = carga["modo"]
                self.publicar_modo()

    def ts_ms(self) -> int:
        return int(time.time() * 1000) + self.offset_reloj_ms

    def publicar_estado(self, en_linea: bool) -> None:
        self._publicar("estado", {
            "codigo": self.codigo, "en_linea": en_linea, "firmware": "3.0.0-sim",
            "ip": "127.0.0.1", "mac": "24:6F:28:AA:BB:CC", "rssi_dbm": random.randint(-70, -45),
            "boot_id": self.boot_id, "armado": True, "modo": self.modo,
        }, qos=1, retener=True)

    def publicar_modo(self) -> None:
        self._publicar("modo", {
            "modo": self.modo, "fuente": "INERCIAL", "ts_ms": self.ts_ms(), "contacto": None,
        }, qos=1, retener=True)

    def publicar_sync(self) -> None:
        self._publicar("sync/req", {"id": self.seq, "t0": int(time.time() * 1000)}, qos=0)

    def publicar_gps(self) -> None:
        self._publicar("gps", self.recorrido.gps(self.ts_ms(), self.modo), qos=0)

    def publicar_telemetria(self) -> None:
        self.seq += 1
        self._publicar("telemetria", {
            "codigo": self.codigo, "firmware": "3.0.0-sim", "mac": "24:6F:28:AA:BB:CC",
            "ip": "127.0.0.1", "boot_id": self.boot_id, "seq": self.seq,
            "uptime_ms": self.seq * 1000, "ts_ms": self.ts_ms(), "ts_fuente": "SERVIDOR",
            "armado": True, "modo": self.modo, "modo_fuente": "INERCIAL", "contacto": None,
            "wifi": {"rssi_dbm": random.randint(-75, -40)},
            "sistema": {"ram_libre_kb": random.randint(180, 260), "temp_c": round(random.uniform(28, 38), 1)},
            "gps": self.recorrido.gps_bloque(),
            "imu": {
                "ax": 0.0, "ay": 0.0, "az": 1.0,
                "a_max_g": round(random.uniform(0.95, 1.3), 3),
                "vib_max_g": round(random.uniform(0.005, 0.05), 4),
                "variacion_g": round(random.uniform(0.01, 0.2) if self.modo == "EN_MARCHA"
                                     else random.uniform(0.001, 0.03), 4),
                "vib_rms_g": round(random.uniform(0.01, 0.04), 4),
                "vib_pp_g": round(random.uniform(0.04, 0.12), 3),
                "giro_max_dps": round(random.uniform(0, 25), 1),
                "pitch_deg": round(random.uniform(-2, 2), 2),
                "roll_deg": round(random.uniform(-2, 2), 2),
                "muestras": 500,
            },
            "audio": {
                "rms_dbfs": round(random.uniform(-80, -60), 2),
                "spl_db": round(random.uniform(40, 62), 2),
                "spl_pico_db": round(random.uniform(62, 85), 2),
                "piso_baja_db": round(random.uniform(-58, -52), 2),
                "piso_alta_db": round(random.uniform(-59, -54), 2),
                "fft_us": random.randint(900, 1800),
                "muestras": 16000,
            },
            "bateria": {"v": round(random.uniform(3.6, 4.1), 2), "pct": random.randint(40, 100)},
            "mqtt": {
                "reconexiones": 0, "publicados": self.publicados, "descartados": 0,
                "rtt_sync_ms": random.randint(3, 15),
            },
        }, qos=0)

    def publicar_evento(self, indice: int, uid: str | None = None) -> tuple:
        """Publica un evento del guion.

        :param uid: ``evento_uid`` a reutilizar. Pasando el mismo uid dos veces
            se reproduce exactamente lo que hace QoS 1 al reentregar un mensaje,
            que es lo que debe deduplicar el servidor.
        :return: ``(tipo, evento_uid)``
        """
        guion = GUION_EVENTOS[indice % len(GUION_EVENTOS)]
        # El modo del guión manda: un CHOQUE solo ocurre en marcha y una ROTURA
        # solo estacionado, igual que en el firmware.
        if self.modo != guion["modo"]:
            self.modo = guion["modo"]
            self.publicar_modo()

        if uid is None:
            self.num_evento += 1
            uid = f"{uuid.uuid4().hex[:8].upper()}-{self.num_evento}"

        self._publicar("evento", {
            "codigo": self.codigo,
            "evento_uid": uid,
            "tipo": guion["tipo"], "nivel": guion["nivel"],
            "ts_ms": self.ts_ms(), "ts_fuente": "SERVIDOR",
            "modo": guion["modo"], "armado": True,
            "evidencia": guion["evidencia"],
            "aceleracion_g": guion["aceleracion_g"], "nivel_audio_db": guion["nivel_audio_db"],
            "gps": self.recorrido.gps_bloque(),
        }, qos=1)
        return guion["tipo"], uid

    def periodo_gps_s(self) -> float:
        return 0.2 if self.modo == "EN_MARCHA" else 1.0


class Command(BaseCommand):
    help = "Emula el nodo ESP32-S3 v3.0.0 publicando por MQTT (sin hardware)."

    def add_arguments(self, parser):
        parser.add_argument("--codigo", default="NODO-01")
        parser.add_argument("--segundos", type=int, default=60)
        parser.add_argument("--eventos", type=int, default=3)
        parser.add_argument(
            "--http", action="store_true",
            help="Usa el camino HTTP de respaldo (POST /api/v1/...) en vez de MQTT.",
        )
        parser.add_argument(
            "--url", default="http://127.0.0.1:8000",
            help="URL del servidor para el modo --http.",
        )
        parser.add_argument(
            "--duplicar-eventos", action="store_true",
            help="Reenvía cada evento una segunda vez, para probar la deduplicación por evento_uid.",
        )

    def handle(self, *args, **opciones):
        if not opciones["http"]:
            try:
                import paho.mqtt.client  # noqa: F401
            except ImportError as exc:
                raise CommandError(
                    "paho-mqtt no está instalado (pip install -r requirements.txt). "
                    "Usa --http para probar el camino de respaldo."
                ) from exc

        simulador = SimuladorNodo(
            opciones["codigo"], self.stdout.write,
            usar_http=opciones["http"], url_base=opciones["url"],
        )
        segundos = opciones["segundos"]
        n_eventos = opciones["eventos"]
        instantes_evento = (
            sorted(random.sample(range(2, segundos + 1), min(n_eventos, max(0, segundos - 1))))
            if segundos > 1 else []
        )

        try:
            simulador.conectar()
        except OSError as exc:
            raise CommandError(
                f"No se pudo conectar al broker MQTT {config.mqtt.host}:{config.mqtt.puerto} — {exc}. "
                "¿Arrancaste infra\\mosquitto\\iniciar_mosquitto.bat?"
            ) from exc

        self.stdout.write(f"Duración: {segundos} s · eventos en los segundos {instantes_evento}")
        indice_evento = 0
        proximo_gps = time.monotonic()
        proximo_sync = time.monotonic()

        try:
            for t in range(1, segundos + 1):
                inicio = time.monotonic()
                fin_segundo = inicio + 1.0

                # Telemetría de 1 s.
                simulador.publicar_telemetria()
                self.stdout.write(
                    f"  [{t:>4}s] telemetria seq={simulador.seq} modo={simulador.modo}"
                )

                # Eventos del guion.
                if t in instantes_evento:
                    tipo, uid = simulador.publicar_evento(indice_evento)
                    self.stdout.write(self.style.WARNING(f"  [{t:>4}s] evento {tipo} uid={uid} (QoS 1)"))
                    if opciones["duplicar_eventos"]:
                        # El MISMO uid: el servidor debe descartar el segundo.
                        simulador.publicar_evento(indice_evento, uid=uid)
                        self.stdout.write(
                            f"  [{t:>4}s] evento {tipo} uid={uid} reenviado "
                            "(el servidor debe deduplicarlo)"
                        )
                    indice_evento += 1

                # GPS a 5 Hz / 1 Hz y sync cada 30 s, dentro de este segundo.
                while time.monotonic() < fin_segundo:
                    ahora = time.monotonic()
                    if ahora >= proximo_gps:
                        simulador.recorrido.avanzar(simulador.periodo_gps_s(), simulador.modo == "EN_MARCHA")
                        simulador.publicar_gps()
                        proximo_gps = ahora + simulador.periodo_gps_s()
                    if ahora >= proximo_sync:
                        simulador.publicar_sync()
                        proximo_sync = ahora + (0.3 if simulador._syncs_pendientes > 0 else 30.0)
                        if simulador._syncs_pendientes > 0:
                            simulador._syncs_pendientes -= 1
                    time.sleep(0.02)
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("\nInterrumpido por el usuario."))
        finally:
            simulador.desconectar()

        self.stdout.write(self.style.SUCCESS(
            f"Simulación finalizada: {simulador.publicados} mensajes publicados."
        ))
