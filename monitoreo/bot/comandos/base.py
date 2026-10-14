"""Clase base de los comandos del bot (patrón comando)."""
from __future__ import annotations

from monitoreo.bot.autorizacion import MULTIPLE, ServicioAccesos
from monitoreo.bot.teclados import FabricaTeclados


class ComandoBase:
    """Cada comando declara su nombre (sin barra), descripción y el permiso
    requerido. ``ejecutar`` siempre responde por sí mismo (no devuelve datos)."""

    nombre = ""
    descripcion = ""
    requiere_vinculacion = True
    permiso = "consultar"  # "consultar" | "controlar" | "invitar" | None (público)

    def __init__(self):
        self._accesos = ServicioAccesos()
        self._teclados = FabricaTeclados()

    def ejecutar(self, ctx) -> None:
        raise NotImplementedError

    def tiene_permiso(self, ctx, vehiculo) -> bool:
        if self.permiso == "controlar":
            return self._accesos.puede_controlar(ctx.chat_id, vehiculo)
        if self.permiso == "invitar":
            return self._accesos.puede_invitar(ctx.chat_id, vehiculo)
        if self.permiso is None:
            return True
        return self._accesos.puede_consultar(ctx.chat_id, vehiculo)

    def resolver_vehiculo_o_responder(self, ctx, placa: str | None = None):
        """Resuelve el vehículo objetivo (o responde el mensaje adecuado y
        devuelve ``None``). Si ``ctx.vehiculo`` ya viene resuelto (callbacks),
        se usa directamente."""
        if ctx.vehiculo is not None:
            vehiculo = ctx.vehiculo
        else:
            vehiculo, error = self._accesos.resolver_vehiculo(ctx.chat_id, placa)
            if vehiculo is None:
                if error == MULTIPLE:
                    vehiculos = self._accesos.vehiculos_de(ctx.chat_id)
                    teclado = self._teclados.seleccion_vehiculo(vehiculos, self.nombre)
                    ctx.cliente.send_message(ctx.chat_id, "¿Para qué vehículo? Elige uno:", reply_markup=teclado)
                else:
                    ctx.cliente.send_message(ctx.chat_id, error)
                return None

        if not self.tiene_permiso(ctx, vehiculo):
            ctx.cliente.send_message(ctx.chat_id, "⛔ No tienes permiso para esta acción en ese vehículo.")
            return None
        return vehiculo

    def resolver_dispositivo_o_responder(self, ctx, placa: str | None = None):
        vehiculo = self.resolver_vehiculo_o_responder(ctx, placa)
        if vehiculo is None:
            return None
        dispositivo = getattr(vehiculo, "dispositivo", None)
        if dispositivo is None:
            ctx.cliente.send_message(ctx.chat_id, "Este vehículo no tiene un dispositivo asignado todavía.")
            return None
        return dispositivo
