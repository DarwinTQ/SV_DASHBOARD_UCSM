"""Estado en memoria del panel en tiempo real (singleton thread-safe).

No se persiste en la base de datos: si el servidor se reinicia, se pierde
(solo es un espejo en memoria para refrescar la web sin recargar).
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

HISTORIAL_MAXIMO = 120  # ~120 s a 1 lectura/segundo


@dataclass
class LecturaDispositivo:
    codigo: str
    ultima_telemetria: dict = field(default_factory=dict)
    historial: deque = field(default_factory=lambda: deque(maxlen=HISTORIAL_MAXIMO))
    ultimo_gps_valido: Optional[dict] = None
    camara_estado: dict = field(default_factory=dict)
    ultimo_frame_url: Optional[str] = None
    fps_vision: Optional[float] = None
    personas_detectadas: int = 0
    latencia_captura_ms: Optional[float] = None


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

    def actualizar_telemetria(self, codigo: str, lectura: dict) -> None:
        with self._lock:
            estado = self._obtener_o_crear(codigo)
            estado.ultima_telemetria = lectura
            estado.historial.append(lectura)
            gps = lectura.get("gps") or {}
            if gps.get("fix"):
                estado.ultimo_gps_valido = gps

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

    def obtener(self, codigo: str) -> Optional[LecturaDispositivo]:
        with self._lock:
            return self._dispositivos.get(codigo)

    def historial_telemetria(self, codigo: str) -> list:
        with self._lock:
            estado = self._dispositivos.get(codigo)
            return list(estado.historial) if estado else []

    def todos(self) -> dict[str, LecturaDispositivo]:
        with self._lock:
            return dict(self._dispositivos)
