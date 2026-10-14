"""/sensores, /bateria."""
from __future__ import annotations

from monitoreo.bot.formateador import FormateadorMensajes

from .base import ComandoBase


class ComandoSensores(ComandoBase):
    nombre = "sensores"
    descripcion = "Vibración, ruido e inclinación actuales"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        lectura = EstadoEnVivo.instancia().obtener(dispositivo.codigo)
        ctx.cliente.send_message(ctx.chat_id, FormateadorMensajes().sensores(lectura))


class ComandoBateria(ComandoBase):
    nombre = "bateria"
    descripcion = "Batería del nodo"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        lectura = EstadoEnVivo.instancia().obtener(dispositivo.codigo)
        ctx.cliente.send_message(ctx.chat_id, FormateadorMensajes().bateria(lectura))
