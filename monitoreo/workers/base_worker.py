"""Clase base para los hilos de fondo del sistema (los arranca main.py)."""
import logging
import threading

from django.db import close_old_connections


class WorkerBase:
    """Hilo con arranque/parada ordenados y manejo de errores por ciclo."""

    nombre = "WorkerBase"

    def __init__(self):
        self._detener = threading.Event()
        self._hilo: threading.Thread | None = None
        self.logger = logging.getLogger(f"monitoreo.workers.{self.nombre}")

    def iniciar(self) -> None:
        if self._hilo is not None and self._hilo.is_alive():
            return
        self._detener.clear()
        self._hilo = threading.Thread(target=self._ejecutar, name=self.nombre, daemon=True)
        self._hilo.start()
        self.logger.info("Worker '%s' iniciado.", self.nombre)

    def detener(self) -> None:
        self._detener.set()
        if self._hilo is not None:
            self._hilo.join(timeout=5)
        self.logger.info("Worker '%s' detenido.", self.nombre)

    def debe_detenerse(self) -> bool:
        return self._detener.is_set()

    def _ejecutar(self) -> None:
        while not self.debe_detenerse():
            try:
                self.ciclo()
            except Exception:
                self.logger.exception("Error no controlado en el ciclo de '%s'.", self.nombre)
            finally:
                close_old_connections()

    def ciclo(self) -> None:
        raise NotImplementedError
