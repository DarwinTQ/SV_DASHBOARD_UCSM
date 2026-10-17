"""Detección con YOLO (singleton, con warm-up), sección 8.1 paso 3.

El modelo de la tesis (``modelos_ia/yolo26_ucsm_best.pt``) **no** tiene la clase
``person``: sus clases son ``abriendo_puerta``, ``arma_blanca``, ``arma_fuego``,
``car broken`` y ``suspicious``. Por eso aquí nada se compara contra ``"person"``
a mano: cada caja se resuelve contra ``config.yolo.clases_vigiladas``, que es lo
que asocia la clase del modelo con un ``TipoEvento`` del catálogo y con su propio
umbral de confianza. Con el modelo genérico (``yolo26n.pt``) la única clase
vigilada es ``person`` y el comportamiento es el de la v2.0.0.
"""
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

    @property
    def vigilada(self):
        """La ``ClaseVigilada`` de esta caja, o ``None`` si no está en el catálogo."""
        return config.yolo.vigilada(self.clase)

    @property
    def genera_incidencia(self) -> bool:
        """La clase está vigilada y la confianza supera **su** umbral."""
        vigilada = self.vigilada
        return vigilada is not None and self.confianza >= vigilada.confianza_alerta

    @property
    def tipo_evento(self) -> Optional[str]:
        vigilada = self.vigilada
        return vigilada.tipo_evento if vigilada else None


@dataclass
class ResultadoDeteccion:
    cajas: list = field(default_factory=list)
    imagen_anotada_jpg: Optional[bytes] = None
    tiempo_inferencia_ms: Optional[float] = None

    # ------------------------------------------------------------------ #
    # Detecciones que sí ameritan una incidencia
    # ------------------------------------------------------------------ #
    @property
    def cajas_alerta(self) -> list:
        """Cajas de clases vigiladas que superan su umbral, de mayor a menor."""
        return sorted(
            (c for c in self.cajas if c.genera_incidencia), key=lambda c: c.confianza, reverse=True,
        )

    @property
    def hay_alerta(self) -> bool:
        return bool(self.cajas_alerta)

    @property
    def caja_principal(self) -> Optional[CajaDeteccion]:
        """La detección más confiable: la que da el tipo de la incidencia."""
        cajas = self.cajas_alerta
        return cajas[0] if cajas else None

    @property
    def clase_principal(self) -> Optional[str]:
        caja = self.caja_principal
        return caja.clase if caja else None

    @property
    def tipo_evento_principal(self) -> Optional[str]:
        caja = self.caja_principal
        return caja.tipo_evento if caja else None

    @property
    def num_detecciones(self) -> int:
        return len(self.cajas_alerta)

    @property
    def clases_detectadas(self) -> list:
        """Clases vigiladas presentes, sin repetir y en orden de confianza."""
        vistas: list[str] = []
        for caja in self.cajas_alerta:
            if caja.clase not in vistas:
                vistas.append(caja.clase)
        return vistas

    @property
    def resumen_clases(self) -> str:
        """``"arma_blanca 86%, suspicious 61%"`` para el texto de Telegram."""
        mejor: dict[str, float] = {}
        for caja in self.cajas_alerta:
            mejor[caja.clase] = max(mejor.get(caja.clase, 0.0), caja.confianza)
        return ", ".join(f"{clase} {conf:.0%}" for clase, conf in mejor.items())

    # ------------------------------------------------------------------ #
    # Nombres heredados de la v2.0.0 (API y plantillas los siguen usando)
    # ------------------------------------------------------------------ #
    @property
    def confianza_max(self) -> Optional[float]:
        caja = self.caja_principal
        return caja.confianza if caja else None

    @property
    def num_personas(self) -> int:
        """Alias histórico de ``num_detecciones`` (clave fija de la API)."""
        return self.num_detecciones

    @property
    def hay_persona(self) -> bool:
        """``True`` solo si la clase detectada es realmente ``person``.

        Se mantiene con su significado literal porque ``PERSONA`` tiene una
        regla propia (solo ESTACIONADO y armado). Para "¿hay algo que alertar?"
        se usa ``hay_alerta``.
        """
        return any(c.clase == "person" and c.genera_incidencia for c in self.cajas)


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
            logger.info(
                "Modelo YOLO '%s' cargado correctamente. Clases del modelo: %s",
                config.yolo.nombre_modelo, list((self._modelo.names or {}).values()),
            )
            self._avisar_clases_desconocidas()
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

    def _avisar_clases_desconocidas(self) -> None:
        """Un nombre mal escrito en el catálogo o en ``YOLO_CLASES`` es la causa
        más común de "detecta pero no alerta": la caja llega con un nombre que
        no está vigilado y se descarta en silencio. Mejor decirlo al arrancar."""
        del_modelo = set((self._modelo.names or {}).values())

        objetivo = [c for c in config.yolo.clases_objetivo if c not in del_modelo]
        if objetivo:
            logger.warning(
                "YOLO_CLASES tiene clases que el modelo no conoce: %s. Clases del modelo: %s",
                ", ".join(objetivo), sorted(del_modelo),
            )

        vigiladas = {v.clase for v in config.yolo.clases_vigiladas}
        sin_vigilar = sorted(del_modelo - vigiladas)
        if sin_vigilar:
            logger.warning(
                "El modelo tiene clases sin TipoEvento asociado (nunca generarán "
                "incidencia ni alerta): %s. Agrégalas a CATALOGO_CLASES_YOLO en config.py.",
                ", ".join(sin_vigilar),
            )

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
