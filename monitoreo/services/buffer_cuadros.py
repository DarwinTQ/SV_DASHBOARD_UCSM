"""Buffer circular de cuadros del stream MJPEG (sección 5.5).

``ReceptorStreamCamara`` va llenando este buffer con los últimos ~3 s de vídeo
de cada cámara. Sirve para dos cosas distintas:

* ``VigilanciaVisual`` toma siempre el cuadro **más reciente** (ya no hace
  polling a ``/capture``, que costaba cientos de milisegundos por foto);
* ``IncidenciaService`` toma el cuadro **más cercano a ``t_deteccion_sensor``**,
  es decir la imagen de cuando realmente pasó el golpe, sin esperar a que la
  cámara saque una foto nueva. Eso es lo que baja el tiempo de respuesta que se
  mide en ``medicion_tiempo``.

Es un singleton por proceso y thread-safe: lo escribe el hilo del receptor y lo
leen el hilo de visión y el del procesador de eventos.
"""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Optional

from config import config


@dataclass
class CuadroStream:
    """Un JPEG del stream con el instante en que lo recibió el servidor."""

    jpg: bytes
    t_recepcion_ms: int
    t_camara_ms: Optional[int] = None   # cabecera X-Timestamp-Ms (uptime de la cámara)
    ancho: Optional[int] = None
    alto: Optional[int] = None


class BufferCuadros:
    """Cuadros de UNA cámara, acotados por antigüedad."""

    def __init__(self, codigo: str, segundos: float | None = None):
        self.codigo = codigo
        self._segundos = segundos if segundos is not None else config.stream.segundos_buffer
        self._lock = threading.Lock()
        self._cuadros: deque[CuadroStream] = deque(maxlen=200)
        self.recibidos = 0
        self.descartados = 0

    def agregar(self, cuadro: CuadroStream) -> None:
        with self._lock:
            self._cuadros.append(cuadro)
            self.recibidos += 1
            self._purgar(cuadro.t_recepcion_ms)

    def _purgar(self, ahora_ms: int) -> None:
        """Se llama con el lock tomado."""
        limite = ahora_ms - int(self._segundos * 1000)
        while self._cuadros and self._cuadros[0].t_recepcion_ms < limite:
            self._cuadros.popleft()
            self.descartados += 1

    def ultimo(self) -> Optional[CuadroStream]:
        with self._lock:
            return self._cuadros[-1] if self._cuadros else None

    def mas_cercano_a(self, t_objetivo_ms: int | None) -> Optional[CuadroStream]:
        """Cuadro cuyo ``t_recepcion_ms`` está más cerca del instante pedido.

        Sin instante (el nodo mandó ``ts_ms`` nulo) devuelve el más reciente,
        que es la mejor aproximación disponible.
        """
        if t_objetivo_ms is None:
            return self.ultimo()
        with self._lock:
            if not self._cuadros:
                return None
            return min(self._cuadros, key=lambda c: abs(c.t_recepcion_ms - t_objetivo_ms))

    def cantidad(self) -> int:
        with self._lock:
            return len(self._cuadros)

    def vaciar(self) -> None:
        with self._lock:
            self._cuadros.clear()

    def resumen(self) -> dict:
        with self._lock:
            cuadros = len(self._cuadros)
            ultimo = self._cuadros[-1] if self._cuadros else None
            antiguedad = self._cuadros[-1].t_recepcion_ms - self._cuadros[0].t_recepcion_ms if cuadros > 1 else 0
        return {
            "codigo": self.codigo, "cuadros": cuadros,
            "ventana_ms": antiguedad, "recibidos": self.recibidos,
            "descartados": self.descartados,
            "bytes_ultimo": len(ultimo.jpg) if ultimo else 0,
        }


class RegistroBuffers:
    """Un ``BufferCuadros`` por código de dispositivo (singleton del proceso)."""

    _instancia: Optional["RegistroBuffers"] = None
    _instancia_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._buffers: dict[str, BufferCuadros] = {}

    @classmethod
    def instancia(cls) -> "RegistroBuffers":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    def para(self, codigo: str) -> BufferCuadros:
        with self._lock:
            if codigo not in self._buffers:
                self._buffers[codigo] = BufferCuadros(codigo)
            return self._buffers[codigo]

    def existente(self, codigo: str) -> Optional[BufferCuadros]:
        """Como ``para`` pero sin crear: para saber si hay stream de verdad."""
        with self._lock:
            return self._buffers.get(codigo)

    def todos(self) -> dict[str, BufferCuadros]:
        with self._lock:
            return dict(self._buffers)
