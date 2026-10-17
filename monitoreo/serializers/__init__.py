from .comunes import GpsPosicionSerializer
from .estado import EstadoCamaraSerializer, EstadoNodoSerializer, ModoSerializer, SyncPeticionSerializer
from .evento import EventoSerializer
from .telemetria import TelemetriaSerializer

__all__ = [
    "TelemetriaSerializer",
    "EventoSerializer",
    "GpsPosicionSerializer",
    "EstadoNodoSerializer",
    "EstadoCamaraSerializer",
    "ModoSerializer",
    "SyncPeticionSerializer",
]
