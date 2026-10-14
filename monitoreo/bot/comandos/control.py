"""/armar, /desarmar (con confirmación)."""
from __future__ import annotations

from .base import ComandoBase


class ComandoArmar(ComandoBase):
    nombre = "armar"
    descripcion = "Arma la vigilancia (con confirmación)"
    permiso = "controlar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        if dispositivo.armado:
            ctx.cliente.send_message(ctx.chat_id, f"🔒 {dispositivo.codigo} ya está armado.")
            return
        teclado = self._teclados.confirmar_control("armar", dispositivo.pk)
        ctx.cliente.send_message(
            ctx.chat_id, f"¿Confirmas ARMAR la vigilancia de {dispositivo.vehiculo.placa}?", reply_markup=teclado,
        )


class ComandoDesarmar(ComandoBase):
    nombre = "desarmar"
    descripcion = "Desarma la vigilancia (con confirmación)"
    permiso = "controlar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        if not dispositivo.armado:
            ctx.cliente.send_message(ctx.chat_id, f"🔓 {dispositivo.codigo} ya está desarmado.")
            return
        teclado = self._teclados.confirmar_control("desarmar", dispositivo.pk)
        ctx.cliente.send_message(
            ctx.chat_id, f"¿Confirmas DESARMAR la vigilancia de {dispositivo.vehiculo.placa}?", reply_markup=teclado,
        )


def aplicar_control(ctx, dispositivo, armar: bool) -> None:
    """Ejecuta el cambio real (lo llama el comando tras confirmar, o el botón
    '🔓 Desarmar' de una alerta). Avisa a TODOS los accesos del vehículo."""
    from django.utils import timezone

    from monitoreo.models import AccesoTelegram
    from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal

    dispositivo.estado = "ACTIVO" if armar else "INACTIVO"
    dispositivo.save(update_fields=["estado"])
    NotificadorTiempoReal.dispositivo_estado({"codigo": dispositivo.codigo, "armado": dispositivo.armado})

    quien = _nombre_quien(ctx, dispositivo.vehiculo)
    verbo = "armó" if armar else "desarmó"
    hora = timezone.localtime().strftime("%H:%M")
    texto = f"{'🔒' if armar else '🔓'} {quien} {verbo} la vigilancia de {dispositivo.vehiculo.placa} a las {hora}."

    destinatarios = AccesoTelegram.objects.filter(vehiculo=dispositivo.vehiculo, activo=True).exclude(chat_id="")
    for destinatario in destinatarios:
        ctx.cliente.send_message(destinatario.chat_id, texto)


def _nombre_quien(ctx, vehiculo) -> str:
    if ctx.es_admin():
        return "Un administrador"
    acceso = next((a for a in ctx.accesos if a.vehiculo_id == vehiculo.pk), None)
    return acceso.nombres if acceso else "Alguien"
