"""Estado en memoria del bot de Telegram, para mostrarlo en Configuración."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from threading import Lock
from typing import Optional


@dataclass
class _Estado:
    conectado: bool = False
    usuario_bot: str = ""
    ultimo_update: Optional[datetime] = None
    comandos_hoy: int = 0
    dia_contador: Optional[date] = None


class EstadoBot:
    """Singleton en memoria (igual que ``EstadoEnVivo``); no se persiste."""

    _instancia: Optional["EstadoBot"] = None
    _instancia_lock = Lock()

    def __init__(self):
        self._lock = Lock()
        self._estado = _Estado()

    @classmethod
    def instancia(cls) -> "EstadoBot":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    def marcar_conectado(self, usuario_bot: str) -> None:
        with self._lock:
            self._estado.conectado = True
            self._estado.usuario_bot = usuario_bot

    def marcar_desconectado(self) -> None:
        with self._lock:
            self._estado.conectado = False

    def registrar_update(self) -> None:
        from django.utils import timezone

        with self._lock:
            ahora = timezone.now()
            if self._estado.dia_contador != ahora.date():
                self._estado.dia_contador = ahora.date()
                self._estado.comandos_hoy = 0
            self._estado.comandos_hoy += 1
            self._estado.ultimo_update = ahora

    def resumen(self) -> dict:
        with self._lock:
            return {
                "conectado": self._estado.conectado,
                "usuario_bot": self._estado.usuario_bot,
                "ultimo_update": self._estado.ultimo_update,
                "comandos_hoy": self._estado.comandos_hoy,
            }
