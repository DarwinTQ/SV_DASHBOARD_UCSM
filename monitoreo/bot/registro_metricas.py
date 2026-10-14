"""Registro de comandos atendidos por el bot (sección 9): mide tiempos de
respuesta por tipo de comando sin agregar tablas nuevas a la base de datos."""
from __future__ import annotations

import csv
import hashlib
from threading import Lock
from typing import Optional

from django.utils import timezone

from config import config


class RegistroComandosBot:
    """Escribe ``logs/bot_comandos.csv`` con lock (un hilo a la vez)."""

    _instancia: "Optional[RegistroComandosBot]" = None
    _instancia_lock = Lock()

    COLUMNAS = [
        "fecha_hora", "chat_id_hash", "rol", "comando", "placa", "exito",
        "tiempo_respuesta_ms", "tiempo_captura_ms", "tiempo_inferencia_ms", "error",
    ]

    def __init__(self):
        self._lock = Lock()
        self._ruta = config.BASE_DIR / "logs" / "bot_comandos.csv"

    @classmethod
    def instancia(cls) -> "RegistroComandosBot":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    @staticmethod
    def _hash_chat(chat_id: str) -> str:
        return hashlib.sha256(chat_id.encode("utf-8")).hexdigest()[:12]

    def registrar(self, *, chat_id: str, rol: str, comando: str, exito: bool, tiempo_respuesta_ms: float,
                   placa: str = "", tiempo_captura_ms=None, tiempo_inferencia_ms=None, error: str = "") -> None:
        fila = [
            timezone.now().isoformat(), self._hash_chat(chat_id), rol, comando, placa, exito,
            round(tiempo_respuesta_ms, 1), tiempo_captura_ms, tiempo_inferencia_ms, error,
        ]
        with self._lock:
            self._ruta.parent.mkdir(parents=True, exist_ok=True)
            es_nuevo = not self._ruta.exists()
            with open(self._ruta, "a", newline="", encoding="utf-8") as archivo:
                escritor = csv.writer(archivo)
                if es_nuevo:
                    escritor.writerow(self.COLUMNAS)
                escritor.writerow(fila)
