"""Detección de personas con YOLO (singleton, con warm-up), sección 8.1 paso 3."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from config import config

logger = logging.getLogger("monitoreo.yolo")


@dataclass
class CajaDeteccion:
    clase: str
    confianza: float
    x1: int
    y1: int
    x2: int
    y2: int


@dataclass
class ResultadoDeteccion:
    cajas: list = field(default_factory=list)
    imagen_anotada_jpg: Optional[bytes] = None
    tiempo_inferencia_ms: Optional[float] = None

    @property
    def hay_persona(self) -> bool:
        return any(c.clase == "person" and c.confianza >= config.yolo.confianza_alerta for c in self.cajas)

    @property
    def confianza_max(self) -> Optional[float]:
        personas = [c.confianza for c in self.cajas if c.clase == "person"]
        return max(personas) if personas else None

    @property
    def num_personas(self) -> int:
        return sum(1 for c in self.cajas if c.clase == "person" and c.confianza >= config.yolo.confianza_alerta)


class DetectorYOLO:
    """Carga el modelo una sola vez. Si falla, el sistema sigue sin visión."""

    _instancia: "Optional[DetectorYOLO]" = None
    _instancia_lock = threading.Lock()

    def __init__(self):
        self._lock = threading.Lock()
        self._modelo = None
        self._disponible = False
        self._cargar_modelo()

    @classmethod
    def instancia(cls) -> "DetectorYOLO":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    @property
    def disponible(self) -> bool:
        return self._disponible

    def _cargar_modelo(self) -> None:
        try:
            from ultralytics import YOLO

            ruta = config.yolo.ruta_modelo
            if not ruta.exists():
                ruta.parent.mkdir(parents=True, exist_ok=True)
                logger.warning(
                    "No se encontró el peso YOLO en %s; ultralytics lo descargará ahí mismo.", ruta,
                )
            self._modelo = YOLO(str(ruta))
            self._disponible = True
            logger.info("Modelo YOLO '%s' cargado correctamente.", config.yolo.nombre_modelo)
        except Exception:
            logger.exception("No se pudo cargar YOLO; el sistema seguirá funcionando sin detección de personas.")
            self._modelo = None
            self._disponible = False

    def calentar(self) -> None:
        if not self._disponible:
            return
        try:
            import numpy as np

            negro = np.zeros((config.yolo.tamano_imagen, config.yolo.tamano_imagen, 3), dtype="uint8")
            self.detectar(negro)
            logger.info("Warm-up de YOLO completado.")
        except Exception:
            logger.exception("Falló el warm-up de YOLO (no crítico).")

    def _indices_clases_objetivo(self):
        if not config.yolo.clases_objetivo:
            return None
        nombres = self._modelo.names
        indices = [i for i, nombre in nombres.items() if nombre in config.yolo.clases_objetivo]
        return indices or None

    def detectar(self, imagen) -> ResultadoDeteccion:
        """``imagen`` puede ser bytes JPEG o un ndarray (usado en el warm-up)."""
        if not self._disponible:
            return ResultadoDeteccion()

        frame = imagen
        if isinstance(imagen, bytes):
            try:
                import cv2
                import numpy as np

                arreglo = np.frombuffer(imagen, dtype="uint8")
                frame = cv2.imdecode(arreglo, cv2.IMREAD_COLOR)
            except Exception:
                logger.exception("No se pudo decodificar la imagen JPEG para YOLO.")
                return ResultadoDeteccion()

        if frame is None:
            return ResultadoDeteccion()

        with self._lock:
            inicio = time.monotonic()
            try:
                salida = self._modelo.predict(
                    frame, imgsz=config.yolo.tamano_imagen, device=config.yolo.dispositivo,
                    conf=config.yolo.confianza_minima, classes=self._indices_clases_objetivo(),
                    verbose=False,
                )
            except Exception:
                logger.exception("Error durante la inferencia de YOLO.")
                return ResultadoDeteccion()
            tiempo_ms = round((time.monotonic() - inicio) * 1000, 2)

        cajas: list[CajaDeteccion] = []
        imagen_anotada = None
        if salida:
            resultado = salida[0]
            for caja in resultado.boxes:
                clase_id = int(caja.cls[0])
                nombre_clase = resultado.names.get(clase_id, str(clase_id))
                confianza = float(caja.conf[0])
                x1, y1, x2, y2 = [int(v) for v in caja.xyxy[0].tolist()]
                cajas.append(CajaDeteccion(clase=nombre_clase, confianza=confianza, x1=x1, y1=y1, x2=x2, y2=y2))
            if cajas:
                try:
                    import cv2

                    anotado = resultado.plot()
                    ok, buffer = cv2.imencode(".jpg", anotado)
                    if ok:
                        imagen_anotada = buffer.tobytes()
                except Exception:
                    logger.exception("No se pudo generar la imagen anotada de YOLO (no crítico).")

        return ResultadoDeteccion(cajas=cajas, imagen_anotada_jpg=imagen_anotada, tiempo_inferencia_ms=tiempo_ms)
