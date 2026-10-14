"""Límite de frecuencia de comandos y de intentos de vinculación fallidos.

Todo en memoria (singleton por proceso), igual que ``EstadoEnVivo``: no hace
falta persistirlo, solo protege al bot durante su ejecución.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Optional

from config import config

VENTANA_VINCULACION_S = 3600
MAX_INTENTOS_VINCULACION = 5


class LimitadorFrecuencia:
    _instancia: Optional["LimitadorFrecuencia"] = None
    _instancia_lock = Lock()

    def __init__(self):
        self._lock = Lock()
        self._comandos: dict[str, deque] = defaultdict(deque)
        self._intentos_vinculacion: dict[str, deque] = defaultdict(deque)

    @classmethod
    def instancia(cls) -> "LimitadorFrecuencia":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    def permitir_comando(self, chat_id: str) -> bool:
        limite = config.telegram.comandos_por_minuto
        with self._lock:
            ahora = time.time()
            cola = self._comandos[chat_id]
            while cola and ahora - cola[0] > 60:
                cola.popleft()
            if len(cola) >= limite:
                return False
            cola.append(ahora)
            return True

    def registrar_intento_vinculacion_fallido(self, chat_id: str) -> int:
        with self._lock:
            ahora = time.time()
            cola = self._intentos_vinculacion[chat_id]
            cola.append(ahora)
            while cola and ahora - cola[0] > VENTANA_VINCULACION_S:
                cola.popleft()
            return len(cola)

    def puede_intentar_vinculacion(self, chat_id: str) -> bool:
        with self._lock:
            ahora = time.time()
            cola = self._intentos_vinculacion[chat_id]
            while cola and ahora - cola[0] > VENTANA_VINCULACION_S:
                cola.popleft()
            return len(cola) < MAX_INTENTOS_VINCULACION

    def reiniciar_intentos_vinculacion(self, chat_id: str) -> None:
        with self._lock:
            self._intentos_vinculacion.pop(chat_id, None)
