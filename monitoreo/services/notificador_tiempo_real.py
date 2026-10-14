"""Envío de mensajes al grupo WebSocket 'panel' (Django Channels)."""
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer

logger = logging.getLogger("monitoreo.tiempo_real")

GRUPO_PANEL = "panel"


class NotificadorTiempoReal:
    """Puente entre los servicios/workers (síncronos) y el grupo de Channels."""

    @staticmethod
    def enviar(tipo: str, data: dict) -> None:
        capa = get_channel_layer()
        if capa is None:
            return
        try:
            async_to_sync(capa.group_send)(
                GRUPO_PANEL,
                {"type": "mensaje_panel", "tipo": tipo, "data": data},
            )
        except Exception:
            logger.exception("No se pudo emitir el mensaje '%s' al panel en tiempo real.", tipo)

    @classmethod
    def telemetria(cls, codigo: str, data: dict) -> None:
        cls.enviar("telemetria", {"codigo": codigo, "data": data})

    @classmethod
    def incidencia_nueva(cls, data: dict) -> None:
        cls.enviar("incidencia_nueva", data)

    @classmethod
    def incidencia_actualizada(cls, data: dict) -> None:
        cls.enviar("incidencia_actualizada", data)

    @classmethod
    def dispositivo_estado(cls, data: dict) -> None:
        cls.enviar("dispositivo_estado", data)

    @classmethod
    def frame(cls, data: dict) -> None:
        cls.enviar("frame", data)
