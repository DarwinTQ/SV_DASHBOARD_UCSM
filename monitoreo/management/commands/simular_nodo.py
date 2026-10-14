"""Comando: python manage.py simular_nodo --codigo NODO-01 --url http://127.0.0.1:8000
                                            --segundos 120 --eventos 5

Clase SimuladorNodo: emula el ESP32-S3 real. Envía telemetría (sección 7.2)
cada 1 s con valores realistas (ruido aleatorio, GPS en Arequipa), sincroniza
su "reloj" con el server_ts_ms de la respuesta igual que hace el firmware, y
dispara eventos aleatorios (sección 7.3) con un evento_uid único.
"""
import random
import time
import uuid

import requests
from django.core.management.base import BaseCommand

from config import config

LAT_AREQUIPA = -16.398866
LON_AREQUIPA = -71.536961

TIPOS_EVENTO = [
    ("VIBRACION", 0.82, 0.5, "g"),
    ("RUIDO", 92.0, 85.0, "dB"),
    ("MANIPULACION", 4.5, 3.0, "deg"),
    ("MOVIMIENTO", 12.0, 8.0, "km/h"),
]


class SimuladorNodo:
    """Simula el tráfico HTTP que generaría un ESP32-S3 real."""

    def __init__(self, codigo: str, url_base: str, api_key: str):
        self.codigo = codigo
        self.url_base = url_base.rstrip("/")
        self.api_key = api_key
        self.boot_id = random.randint(10**9, 10**10 - 1)
        self.seq = 0
        self.offset_reloj_ms = 0
        self.sesion = requests.Session()

    def _cabeceras(self) -> dict:
        return {"X-API-Key": self.api_key, "X-Device-Code": self.codigo}

    def _ts_ms(self) -> int:
        return int(time.time() * 1000) + self.offset_reloj_ms

    def _telemetria(self) -> dict:
        self.seq += 1
        lat = LAT_AREQUIPA + random.uniform(-0.0006, 0.0006)
        lon = LON_AREQUIPA + random.uniform(-0.0006, 0.0006)
        return {
            "codigo": self.codigo, "firmware": "2.0.0-sim", "mac": "24:6F:28:AA:BB:CC",
            "ip": "127.0.0.1", "boot_id": self.boot_id, "seq": self.seq,
            "uptime_ms": self.seq * 1000, "ts_ms": self._ts_ms(), "ts_fuente": "SERVIDOR",
            "armado": True,
            "wifi": {"rssi_dbm": random.randint(-75, -40)},
            "sistema": {"ram_libre_kb": random.randint(180, 260), "temp_c": round(random.uniform(28, 38), 1)},
            "gps": {"fix": True, "sats": random.randint(5, 10), "lat": lat, "lon": lon,
                    "vel_kmh": round(random.uniform(0, 5), 1), "alt_m": 2335.0, "hdop": round(random.uniform(0.8, 1.6), 1)},
            "imu": {"ax": 0, "ay": 0, "az": 1, "a_max_g": round(random.uniform(0.95, 1.1), 3),
                    "vib_rms_g": round(random.uniform(0.01, 0.04), 4), "vib_pp_g": round(random.uniform(0.04, 0.12), 3),
                    "giro_max_dps": round(random.uniform(0, 10), 1), "pitch_deg": round(random.uniform(-1, 1), 2),
                    "roll_deg": round(random.uniform(-1, 1), 2), "muestras": 200},
            "audio": {"rms_dbfs": round(random.uniform(-80, -60), 2), "spl_db": round(random.uniform(35, 55), 2),
                      "pico_dbfs": round(random.uniform(-55, -40), 2), "pico_spl_db": round(random.uniform(55, 75), 2),
                      "muestras": 16000},
            "bateria": {"v": round(random.uniform(3.6, 4.1), 2), "pct": random.randint(40, 100)},
            "http": {"rtt_prev_ms": random.randint(8, 40), "ok": self.seq, "fallos": 0, "descartados": 0},
        }

    def enviar_telemetria(self) -> dict:
        resp = self.sesion.post(
            f"{self.url_base}{config.esp32.ruta_telemetria}",
            json=self._telemetria(), headers=self._cabeceras(), timeout=5,
        )
        if resp.ok:
            server_ts_ms = resp.json().get("server_ts_ms")
            if server_ts_ms is not None:
                self.offset_reloj_ms = server_ts_ms - int(time.time() * 1000)
        return {"status": resp.status_code, "cuerpo": resp.text[:200]}

    def disparar_evento_aleatorio(self) -> dict:
        codigo_tipo, valor_base, umbral, unidad = random.choice(TIPOS_EVENTO)
        valor = round(umbral * random.uniform(1.05, 1.6), 2)
        evento = {
            "codigo": self.codigo, "evento_uid": f"{uuid.uuid4().hex[:8].upper()}-{self.seq}",
            "tipo": codigo_tipo, "ts_ms": self._ts_ms(), "ts_fuente": "SERVIDOR",
            "valor": valor, "umbral": umbral, "unidad": unidad,
            "aceleracion_g": round(random.uniform(0.8, 1.6), 2),
            "nivel_audio_db": round(random.uniform(60, 95), 1),
            "gps": {"fix": True, "sats": 7, "lat": LAT_AREQUIPA, "lon": LON_AREQUIPA, "vel_kmh": 0, "alt_m": 2335, "hdop": 1.1},
            "imu": {"pitch_deg": round(random.uniform(-5, 5), 1), "roll_deg": round(random.uniform(-5, 5), 1)},
            "wifi": {"rssi_dbm": random.randint(-75, -40)}, "bateria": {"v": 3.9, "pct": 65},
        }
        resp = self.sesion.post(
            f"{self.url_base}{config.esp32.ruta_eventos}",
            json=evento, headers=self._cabeceras(), timeout=5,
        )
        return {"tipo": codigo_tipo, "status": resp.status_code, "cuerpo": resp.text[:200]}


class Command(BaseCommand):
    help = "Simula un nodo ESP32-S3 enviando telemetría y eventos (sin hardware)."

    def add_arguments(self, parser):
        parser.add_argument("--codigo", default="NODO-01")
        parser.add_argument("--url", default="http://127.0.0.1:8000")
        parser.add_argument("--segundos", type=int, default=60)
        parser.add_argument("--eventos", type=int, default=3)

    def handle(self, *args, **options):
        simulador = SimuladorNodo(options["codigo"], options["url"], config.esp32.api_key)
        segundos = options["segundos"]
        n_eventos = options["eventos"]
        instantes_evento = sorted(random.sample(range(1, segundos + 1), min(n_eventos, segundos))) if segundos else []

        self.stdout.write(f"Simulando '{simulador.codigo}' contra {options['url']} durante {segundos} s…")
        for t in range(1, segundos + 1):
            inicio = time.time()
            try:
                resultado = simulador.enviar_telemetria()
                self.stdout.write(f"  [{t:>4}s] telemetria -> {resultado['status']}")
            except requests.RequestException as exc:
                self.stdout.write(self.style.ERROR(f"  [{t:>4}s] error de red en telemetria: {exc}"))

            if t in instantes_evento:
                try:
                    resultado = simulador.disparar_evento_aleatorio()
                    self.stdout.write(self.style.WARNING(
                        f"  [{t:>4}s] evento {resultado['tipo']} -> {resultado['status']}"
                    ))
                except requests.RequestException as exc:
                    self.stdout.write(self.style.ERROR(f"  [{t:>4}s] error de red en evento: {exc}"))

            transcurrido = time.time() - inicio
            time.sleep(max(0.0, 1.0 - transcurrido))

        self.stdout.write(self.style.SUCCESS("Simulación finalizada."))
