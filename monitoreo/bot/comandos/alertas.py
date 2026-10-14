"""/alertas on|off, /silenciar."""
from __future__ import annotations

from django.utils import timezone

from .base import ComandoBase


class ComandoAlertas(ComandoBase):
    nombre = "alertas"
    descripcion = "Activa/desactiva tus alertas: /alertas on|off"
    permiso = None

    def ejecutar(self, ctx) -> None:
        if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
            ctx.cliente.send_message(ctx.chat_id, "Uso: /alertas on  ó  /alertas off")
            return
        if not ctx.accesos:
            ctx.cliente.send_message(ctx.chat_id, "No tienes accesos vinculados.")
            return

        activar = ctx.args[0].lower() == "on"
        for acceso in ctx.accesos:
            acceso.recibe_alertas = activar
            acceso.save(update_fields=["recibe_alertas"])
        ctx.cliente.send_message(
            ctx.chat_id, f"🔔 Alertas {'activadas' if activar else 'desactivadas'} para todos tus vehículos.",
        )


class ComandoSilenciar(ComandoBase):
    nombre = "silenciar"
    descripcion = "Pausa tus alertas: /silenciar <minutos> (0 reanuda)"
    permiso = None

    def ejecutar(self, ctx) -> None:
        if not ctx.args or not ctx.args[0].isdigit():
            ctx.cliente.send_message(ctx.chat_id, "Uso: /silenciar <minutos> (máximo 720; 0 reanuda)")
            return
        if not ctx.accesos:
            ctx.cliente.send_message(ctx.chat_id, "No tienes accesos vinculados.")
            return

        minutos = min(int(ctx.args[0]), 720)
        hasta = timezone.now() + timezone.timedelta(minutes=minutos) if minutos > 0 else None
        for acceso in ctx.accesos:
            acceso.silenciado_hasta = hasta
            acceso.save(update_fields=["silenciado_hasta"])

        if minutos > 0:
            ctx.cliente.send_message(ctx.chat_id, f"🔕 Alertas silenciadas por {minutos} min.")
        else:
            ctx.cliente.send_message(ctx.chat_id, "🔔 Alertas reanudadas.")
