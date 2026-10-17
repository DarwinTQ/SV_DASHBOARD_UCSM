"""Armar / desarmar un dispositivo desde cualquier origen (sección 5.3).

Hay tres puertas de entrada al mismo cambio —la web, la API del panel y el bot
de Telegram (``/armar`` y ``/desarmar``)— y las tres tienen que hacer lo mismo:

1. guardar ``dispositivo.estado``;
2. avisar al panel por WebSocket;
3. **publicar ``ucsm/v1/<codigo>/config`` retenido** para que el nodo se entere.

El paso 3 es el que hace que el cambio llegue de verdad al ESP32-S3. Al ser un
mensaje retenido, el nodo lo recibe aunque en ese momento esté desconectado:
el broker se lo entrega en cuanto vuelve.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from monitoreo.services.mqtt_service import PublicadorConfiguracion
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal

logger = logging.getLogger("monitoreo.servicios")


@dataclass
class ResultadoControl:
    armado: bool
    cambio: bool
    publicado_mqtt: bool

    @property
    def mensaje(self) -> str:
        texto = "armado" if self.armado else "desarmado"
        if self.publicado_mqtt:
            return f"Dispositivo {texto}; la configuración se envió al nodo."
        return (
            f"Dispositivo {texto}. Sin conexión al broker MQTT en este momento: "
            "el nodo lo recibirá al reconectarse (config es un mensaje retenido)."
        )


class ControlArmadoService:
    """Único camino para cambiar el armado de un dispositivo."""

    def __init__(self, publicador: PublicadorConfiguracion | None = None):
        self._publicador = publicador or PublicadorConfiguracion.instancia()

    def aplicar(self, dispositivo, armar: bool) -> ResultadoControl:
        cambio = dispositivo.armado != armar
        dispositivo.estado = "ACTIVO" if armar else "INACTIVO"
        dispositivo.save(update_fields=["estado"])

        NotificadorTiempoReal.dispositivo_estado({
            "codigo": dispositivo.codigo, "armado": dispositivo.armado,
        })
        publicado = self._publicador.publicar(dispositivo)
        logger.info("Dispositivo %s %s (config publicada: %s)",
                    dispositivo.codigo, "armado" if armar else "desarmado", publicado)
        return ResultadoControl(armado=dispositivo.armado, cambio=cambio, publicado_mqtt=publicado)

    def republicar_umbrales(self) -> int:
        """Tras cambiar los umbrales en la página Configuración."""
        return self._publicador.publicar_todos()
