"""Worker que consume la cola de eventos de sensor y dispara el pipeline.

La API (``eventos_api.py``) solo encola y responde 202 de inmediato; este
worker (arrancado únicamente por ``main.py``) es quien ejecuta el pipeline
completo (captura, YOLO, BD, Telegram) definido en ``IncidenciaService``
(Fase 3).
"""
from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from threading import Lock

from .base_worker import WorkerBase


@dataclass
class ResultadoEncolado:
    encolado: bool
    duplicado: bool


class CacheEventosRecientes:
    """Detecta ``evento_uid`` repetidos durante una ventana de tiempo (10 min)."""

    def __init__(self, ventana_s: int = 600):
        self._ventana_s = ventana_s
        self._vistos: dict[str, float] = {}
        self._lock = Lock()

    def ya_visto(self, evento_uid: str) -> bool:
        with self._lock:
            self._purgar()
            return evento_uid in self._vistos

    def registrar(self, evento_uid: str) -> None:
        with self._lock:
            self._vistos[evento_uid] = time.time()

    def _purgar(self) -> None:
        limite = time.time() - self._ventana_s
        for uid in [u for u, t in self._vistos.items() if t < limite]:
            del self._vistos[uid]


class ProcesadorEventos(WorkerBase):
    """Cola de eventos de sensor (hilo único de consumo, FIFO)."""

    nombre = "ProcesadorEventos"
    _instancia: "ProcesadorEventos | None" = None
    _instancia_lock = Lock()

    def __init__(self, tamano_cola: int = 50):
        super().__init__()
        self._cola: "queue.Queue[dict]" = queue.Queue(maxsize=tamano_cola)
        self._cache_duplicados = CacheEventosRecientes()

    @classmethod
    def instancia(cls) -> "ProcesadorEventos":
        with cls._instancia_lock:
            if cls._instancia is None:
                from config import config

                cls._instancia = cls(tamano_cola=config.procesamiento.tamano_cola)
            return cls._instancia

    def es_duplicado(self, evento_uid: str) -> bool:
        return self._cache_duplicados.ya_visto(evento_uid)

    def encolar(self, evento: dict) -> ResultadoEncolado:
        evento_uid = evento.get("evento_uid")
        if evento_uid and self.es_duplicado(evento_uid):
            return ResultadoEncolado(encolado=False, duplicado=True)
        if evento_uid:
            self._cache_duplicados.registrar(evento_uid)
        try:
            self._cola.put_nowait(evento)
            return ResultadoEncolado(encolado=True, duplicado=False)
        except queue.Full:
            self.logger.error("Cola de eventos llena; se descarta evento_uid=%s", evento_uid)
            return ResultadoEncolado(encolado=False, duplicado=False)

    def ciclo(self) -> None:
        try:
            evento = self._cola.get(timeout=1)
        except queue.Empty:
            return
        try:
            self._procesar(evento)
        finally:
            self._cola.task_done()

    def _procesar(self, evento: dict) -> None:
        from monitoreo.services.incidencia_service import IncidenciaService

        IncidenciaService().procesar_evento_sensor(evento)
