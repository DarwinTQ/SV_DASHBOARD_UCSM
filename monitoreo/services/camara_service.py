"""Clientes de la ESP32-CAM (real y simulado), sección 7.4."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests

from config import config

logger = logging.getLogger("monitoreo.servicios")


@dataclass
class ResultadoCaptura:
    exito: bool
    jpg: Optional[bytes] = None
    ancho: Optional[int] = None
    alto: Optional[int] = None
    captura_ms: Optional[float] = None
    error: Optional[str] = None


class ClienteCamaraESP32:
    """Cliente HTTP de la cámara real. Un ``threading.Lock`` por dispositivo
    evita que el pipeline de eventos y la vigilancia visual choquen (la
    ESP32-CAM solo atiende un cliente a la vez)."""

    _locks: dict[str, threading.Lock] = {}
    _locks_lock = threading.Lock()

    def __init__(self, codigo_dispositivo: str):
        self.codigo = codigo_dispositivo
        self._url_base = config.url_camara(codigo_dispositivo)

    @classmethod
    def _lock_para(cls, codigo: str) -> threading.Lock:
        with cls._locks_lock:
            if codigo not in cls._locks:
                cls._locks[codigo] = threading.Lock()
            return cls._locks[codigo]

    def disponible(self) -> bool:
        return bool(self._url_base)

    def capturar(self, con_flash: bool = False) -> ResultadoCaptura:
        if not self.disponible():
            return ResultadoCaptura(exito=False, error="cámara no configurada para este dispositivo")

        lock = self._lock_para(self.codigo)
        if not lock.acquire(timeout=config.esp32cam.timeout_s):
            return ResultadoCaptura(exito=False, error="cámara ocupada (timeout esperando el lock)")
        try:
            inicio = time.monotonic()
            params = {"flash": "1"} if (con_flash and config.esp32cam.usar_flash_noche) else {}
            resp = requests.get(
                f"{self._url_base}/capture", params=params,
                headers={"X-API-Key": config.esp32cam.api_key},
                timeout=config.esp32cam.timeout_s,
            )
            resp.raise_for_status()
            try:
                captura_ms = float(resp.headers.get("X-Capture-Ms", 0)) or round((time.monotonic() - inicio) * 1000, 2)
            except ValueError:
                captura_ms = round((time.monotonic() - inicio) * 1000, 2)
            return ResultadoCaptura(
                exito=True, jpg=resp.content,
                ancho=int(resp.headers.get("X-Frame-Width", 0) or 0) or None,
                alto=int(resp.headers.get("X-Frame-Height", 0) or 0) or None,
                captura_ms=captura_ms,
            )
        except requests.RequestException as exc:
            logger.warning("Fallo al capturar de la cámara %s: %s", self.codigo, exc)
            return ResultadoCaptura(exito=False, error=str(exc))
        finally:
            lock.release()

    def obtener_estado(self) -> Optional[dict]:
        if not self.disponible():
            return None
        try:
            resp = requests.get(
                f"{self._url_base}/status", headers={"X-API-Key": config.esp32cam.api_key},
                timeout=config.esp32cam.timeout_s,
            )
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            logger.warning("Fallo al obtener /status de la cámara %s: %s", self.codigo, exc)
            return None


class CamaraSimulada:
    """Para probar sin hardware: usa imágenes de ``media/simulacion/*.jpg``
    (se generan 3 de ejemplo con Pillow si la carpeta está vacía)."""

    def __init__(self, codigo_dispositivo: str):
        self.codigo = codigo_dispositivo

    def disponible(self) -> bool:
        return True

    def _carpeta(self) -> Path:
        carpeta = config.bd.media_root / "simulacion"
        carpeta.mkdir(parents=True, exist_ok=True)
        return carpeta

    def _generar_imagenes_ejemplo_si_falta(self, carpeta: Path) -> None:
        if any(carpeta.glob("*.jpg")):
            return
        from PIL import Image, ImageDraw

        for i in range(3):
            img = Image.new("RGB", (640, 480), color=(35, 35, 35))
            draw = ImageDraw.Draw(img)
            draw.text((20, 20), f"Simulación {i + 1} — {self.codigo}", fill=(255, 255, 255))
            img.save(carpeta / f"simulacion_{i + 1}.jpg", "JPEG")

    def capturar(self, con_flash: bool = False) -> ResultadoCaptura:
        import random

        carpeta = self._carpeta()
        self._generar_imagenes_ejemplo_si_falta(carpeta)
        imagenes = list(carpeta.glob("*.jpg"))
        if not imagenes:
            return ResultadoCaptura(exito=False, error="no hay imágenes de simulación disponibles")
        ruta = random.choice(imagenes)
        time.sleep(random.uniform(0.08, 0.15))
        return ResultadoCaptura(
            exito=True, jpg=ruta.read_bytes(), ancho=640, alto=480,
            captura_ms=round(random.uniform(80, 150), 1),
        )

    def obtener_estado(self) -> dict:
        return {
            "ok": True, "codigo": f"CAM-{self.codigo}", "firmware": "simulado",
            "resolucion": "VGA", "simulada": True,
        }


class FabricaCamara:
    """Devuelve el cliente de cámara correcto según ``config.esp32cam.simulada``."""

    @staticmethod
    def para(codigo_dispositivo: str):
        if config.esp32cam.simulada:
            return CamaraSimulada(codigo_dispositivo)
        return ClienteCamaraESP32(codigo_dispositivo)
