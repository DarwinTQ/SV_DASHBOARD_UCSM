"""/vehiculo, /metricas."""
from __future__ import annotations

from monitoreo.bot.formateador import FormateadorMensajes

from .base import ComandoBase


class ComandoVehiculo(ComandoBase):
    nombre = "vehiculo"
    descripcion = "Datos del vehículo y dispositivo"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        ctx.cliente.send_message(ctx.chat_id, FormateadorMensajes().vehiculo_info(dispositivo))


class ComandoMetricas(ComandoBase):
    nombre = "metricas"
    descripcion = "Resumen de resultados de hoy"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        vehiculo = self.resolver_vehiculo_o_responder(ctx, placa)
        if vehiculo is None:
            return
        from monitoreo.repositories.incidencia_repository import IncidenciaRepository

        kpis = IncidenciaRepository().kpis_hoy()
        ctx.cliente.send_message(ctx.chat_id, FormateadorMensajes().metricas_hoy(kpis))
