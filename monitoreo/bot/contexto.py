"""Contexto de ejecución de un comando o callback del bot."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .cliente_bot_api import ClienteBotAPI


@dataclass
class ContextoComando:
    """Todo lo que un comando necesita para responder, sin acoplarse al
    formato crudo del update de Telegram."""

    chat_id: str
    cliente: ClienteBotAPI
    texto: str = ""
    args: list = field(default_factory=list)
    nombre_telegram: str = ""
    usuario_telegram: str = ""
    accesos: list = field(default_factory=list)
    vehiculo: Optional[object] = None
    acceso_actual: Optional[object] = None
    t_recepcion: Optional[object] = None
    es_callback: bool = False
    callback_query_id: Optional[str] = None
    mensaje_id_origen: Optional[int] = None

    def es_admin(self) -> bool:
        from config import config

        return self.chat_id in config.telegram.admin_chat_ids

    def esta_vinculado(self) -> bool:
        return bool(self.accesos) or self.es_admin()
