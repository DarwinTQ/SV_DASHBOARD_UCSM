"""/ultima, /incidencias."""
from __future__ import annotations

from monitoreo.bot.formateador import FormateadorMensajes

from .base import ComandoBase


class ComandoUltima(ComandoBase):
    nombre = "ultima"
    descripcion = "Última incidencia registrada"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        vehiculo = self.resolver_vehiculo_o_responder(ctx, placa)
        if vehiculo is None:
            return

        from monitoreo.models import Incidencia

        incidencia = (
            Incidencia.objects.filter(dispositivo__vehiculo=vehiculo)
            .select_related("tipo", "dispositivo__vehiculo").order_by("-fecha_hora").first()
        )
        if incidencia is None:
            ctx.cliente.send_message(ctx.chat_id, "No hay incidencias registradas todavía.")
            return
        self._enviar_incidencia(ctx, incidencia)

    def _enviar_incidencia(self, ctx, incidencia) -> None:
        texto = FormateadorMensajes().incidencia_resumen(incidencia)
        imagen_campo = incidencia.imagen_anotada or incidencia.imagen
        puede_controlar = self._accesos.puede_controlar(ctx.chat_id, incidencia.dispositivo.vehiculo)
        teclado = self._teclados.alerta(incidencia, puede_controlar) if incidencia.validacion is None else None

        if imagen_campo:
            with imagen_campo.open("rb") as archivo:
                ctx.cliente.send_photo(ctx.chat_id, archivo.read(), caption=texto, reply_markup=teclado)
        else:
            ctx.cliente.send_message(ctx.chat_id, texto, reply_markup=teclado)


class ComandoIncidencias(ComandoBase):
    nombre = "incidencias"
    descripcion = "Últimas incidencias (hasta 10): /incidencias [n]"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        n = 5
        placa = None
        for arg in ctx.args:
            if arg.isdigit():
                n = min(int(arg), 10)
            else:
                placa = arg

        vehiculo = self.resolver_vehiculo_o_responder(ctx, placa)
        if vehiculo is None:
            return

        from monitoreo.models import Incidencia

        incidencias = list(
            Incidencia.objects.filter(dispositivo__vehiculo=vehiculo)
            .select_related("tipo").order_by("-fecha_hora")[:n]
        )
        ctx.cliente.send_message(ctx.chat_id, FormateadorMensajes().lista_incidencias(incidencias))
