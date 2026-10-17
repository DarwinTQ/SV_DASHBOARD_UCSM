"""Estado en memoria del panel en tiempo real (singleton thread-safe).

No se persiste en la base de datos: si el servidor se reinicia, se pierde
(solo es un espejo en memoria para refrescar la web sin recargar).

Desde la migración a MQTT (v3.0.0) también guarda:
  * el **rastro GPS** de los últimos 300 puntos (el nodo publica a 5 Hz en
    marcha), que alimenta la polilínea del mapa en vivo y ``/ubicacion`` del bot;
  * el **modo** del vehículo (EN_MARCHA / ESTACIONADO) y su fuente, que es lo
    que decide si ``VigilanciaVisual`` puede generar incidencias PERSONA;
  * el estado de conexión que llega por el tópico ``estado`` y su *Last Will*,
    sin esperar ningún timeout.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

HISTORIAL_MAXIMO = 120    # ~120 s a 1 lectura/segundo
RASTRO_MAXIMO = 300       # 300 puntos = 60 s a 5 Hz (sección 5.3)


@dataclass
class PuntoGps:
    """Un punto del rastro, tal como lo publica el nodo en ``.../gps``."""

    lat: float
    lon: float
    vel_kmh: float = 0.0
    rumbo_deg: float = 0.0
    hdop: Optional[float] = None
    sats: Optional[int] = None
    modo: str = ""
    ts_ms: Optional[int] = None
    t_recepcion: float = field(default_factory=time.time)

    def a_dict(self) -> dict:
        return {
            "lat": self.lat, "lon": self.lon, "vel_kmh": self.vel_kmh,
            "rumbo_deg": self.rumbo_deg, "hdop": self.hdop, "sats": self.sats,
            "modo": self.modo, "ts_ms": self.ts_ms,
            "antiguedad_s": round(time.time() - self.t_recepcion, 2),
        }


@dataclass
class LecturaDispositivo:
    codigo: str
    ultima_telemetria: dict = field(default_factory=dict)
    historial: deque = field(default_factory=lambda: deque(maxlen=HISTORIAL_MAXIMO))
    ultimo_gps_valido: Optional[dict] = None
    rastro: deque = field(default_factory=lambda: deque(maxlen=RASTRO_MAXIMO))
    camara_estado: dict = field(default_factory=dict)
    ultimo_frame_url: Optional[str] = None
    fps_vision: Optional[float] = None
    personas_detectadas: int = 0
    latencia_captura_ms: Optional[float] = None
    # --- Estado que llega por MQTT ---
    modo: str = ""
    modo_fuente: str = ""
    contacto: Optional[bool] = None
    en_linea_mqtt: Optional[bool] = None
    estado_mqtt: dict = field(default_factory=dict)
    t_ultimo_mensaje: Optional[float] = None


class EstadoEnVivo:
    """Único punto de lectura/escritura del estado en vivo de todos los dispositivos."""

    _instancia: Optional["EstadoEnVivo"] = None
    _instancia_lock = Lock()

    def __init__(self):
        self._lock = Lock()
        self._dispositivos: dict[str, LecturaDispositivo] = {}

    @classmethod
    def instancia(cls) -> "EstadoEnVivo":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    def _obtener_o_crear(self, codigo: str) -> LecturaDispositivo:
        if codigo not in self._dispositivos:
            self._dispositivos[codigo] = LecturaDispositivo(codigo=codigo)
        return self._dispositivos[codigo]

    # ------------------------------------------------------------------ #
    # Escrituras
    # ------------------------------------------------------------------ #
    def actualizar_telemetria(self, codigo: str, lectura: dict) -> None:
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.ultima_telemetria = lectura
            estado.historial.append(lectura)
            estado.t_ultimo_mensaje = time.time()
            if lectura.get("modo"):
                estado.modo = lectura["modo"]
            if lectura.get("modo_fuente"):
                estado.modo_fuente = lectura["modo_fuente"]
            if "contacto" in lectura:
                estado.contacto = lectura.get("contacto")
            gps = lectura.get("gps") or {}
            if gps.get("fix"):
                estado.ultimo_gps_valido = gps

    def actualizar_gps(self, codigo: str, gps: dict) -> Optional[PuntoGps]:
        """Guarda un punto del tópico ``.../gps``. Devuelve el punto si tenía fix."""
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.t_ultimo_mensaje = time.time()
            if gps.get("modo"):
                estado.modo = gps["modo"]
            if not gps.get("fix"):
                return None

            punto = PuntoGps(
                lat=float(gps["lat"]), lon=float(gps["lon"]),
                vel_kmh=float(gps.get("vel_kmh") or 0.0),
                rumbo_deg=float(gps.get("rumbo_deg") or 0.0),
                hdop=gps.get("hdop"), sats=gps.get("sats"),
                modo=gps.get("modo") or estado.modo, ts_ms=gps.get("ts_ms"),
            )
            estado.rastro.append(punto)
            estado.ultimo_gps_valido = gps
            return punto

    def actualizar_modo(self, codigo: str, modo: str, fuente: str = "", contacto=None) -> None:
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.modo = modo
            estado.t_ultimo_mensaje = time.time()
            if fuente:
                estado.modo_fuente = fuente
            if contacto is not None:
                estado.contacto = contacto

    def actualizar_estado_mqtt(self, codigo: str, estado_mqtt: dict) -> None:
        """Tópico ``.../estado`` (retenido) y su *Last Will*."""
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.estado_mqtt = estado_mqtt
            estado.en_linea_mqtt = bool(estado_mqtt.get("en_linea"))
            estado.t_ultimo_mensaje = time.time()
            if estado_mqtt.get("modo"):
                estado.modo = estado_mqtt["modo"]

    def actualizar_frame(self, codigo: str, url: str, fps: float, personas: int, latencia_captura_ms: float) -> None:
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.ultimo_frame_url = url
            estado.fps_vision = fps
            estado.personas_detectadas = personas
            estado.latencia_captura_ms = latencia_captura_ms

    def actualizar_estado_camara(self, codigo: str, estado_camara: dict) -> None:
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.camara_estado = estado_camara

    def limpiar_rastro(self, codigo: str) -> None:
        with self._lock:
            estado = self._dispositivos.get(codigo)
            if estado:
                estado.rastro.clear()

    # ------------------------------------------------------------------ #
    # Lecturas
    # ------------------------------------------------------------------ #
    def obtener(self, codigo: str) -> Optional[LecturaDispositivo]:
        with self._lock:
            return self._dispositivos.get(codigo)

    def historial_telemetria(self, codigo: str) -> list:
        with self._lock:
            estado = self._dispositivos.get(codigo)
            return list(estado.historial) if estado else []

    def modo(self, codigo: str) -> str:
        with self._lock:
            estado = self._dispositivos.get(codigo)
            return estado.modo if estado else ""

    def esta_estacionado(self, codigo: str) -> bool:
        """``VigilanciaVisual`` solo alerta PERSONA con el vehículo estacionado
        (en marcha siempre hay personas dentro del auto, sección 5.4)."""
        return self.modo(codigo) == "ESTACIONADO"

    def ultimo_punto_gps(self, codigo: str) -> Optional[PuntoGps]:
        with self._lock:
            estado = self._dispositivos.get(codigo)
            if not estado or not estado.rastro:
                return None
            return estado.rastro[-1]

    def rastro(self, codigo: str, segundos: float = 300.0) -> list:
        """Puntos de los últimos ``segundos`` (por defecto 5 min, el rastro del mapa)."""
        limite = time.time() - segundos
        with self._lock:
            estado = self._dispositivos.get(codigo)
            if not estado:
                return []
            return [p.a_dict() for p in estado.rastro if p.t_recepcion >= limite]

    def todos(self) -> dict[str, LecturaDispositivo]:
        with self._lock:
            return dict(self._dispositivos)
