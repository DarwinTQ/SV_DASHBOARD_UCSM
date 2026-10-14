"""Utilidades de tiempo del servidor (todas las marcas en UTC con milisegundos)."""
from datetime import datetime, timezone


class RelojServidor:
    """Centraliza la obtención y conversión de instantes de tiempo."""

    @staticmethod
    def ahora() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def ahora_ms() -> int:
        return int(RelojServidor.ahora().timestamp() * 1000)

    @staticmethod
    def ms_a_datetime(ms: int | None) -> datetime | None:
        if ms is None:
            return None
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
