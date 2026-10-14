"""/invitar, /familia, /revocar."""
from __future__ import annotations

import re

from config import config
from monitoreo.bot.formateador import FormateadorMensajes
from monitoreo.bot.invitaciones import ServicioInvitaciones

from .base import ComandoBase

PATRON_PLACA = re.compile(r"^[A-Z0-9]{3}-?[A-Z0-9]{3}$", re.IGNORECASE)


class ComandoInvitar(ComandoBase):
    nombre = "invitar"
    descripcion = "Invita a un familiar (solo propietario): /invitar <nombre> [parentesco]"
    permiso = "invitar"

    def ejecutar(self, ctx) -> None:
        if not ctx.args:
            ctx.cliente.send_message(ctx.chat_id, "Uso: /invitar <nombre> [parentesco]")
            return

        args = list(ctx.args)
        placa = None
        if len(args) > 1 and PATRON_PLACA.match(args[-1]):
            placa = args.pop()

        nombre, parentesco = args[0], " ".join(args[1:])
        vehiculo = self.resolver_vehiculo_o_responder(ctx, placa)
        if vehiculo is None:
            return

        acceso, error = ServicioInvitaciones().crear_invitacion(vehiculo, nombre, parentesco)
        if error:
            ctx.cliente.send_message(ctx.chat_id, f"⚠️ {error}")
            return

        info_bot = ctx.cliente.get_me()
        usuario_bot = (info_bot.get("result") or {}).get("username", "")
        enlace = ServicioInvitaciones().enlace_invitacion(acceso, usuario_bot) if usuario_bot else ""

        texto = (
            f"✅ Invitación creada para <b>{nombre}</b> ({vehiculo.placa}).\n"
            f"Código: <code>{acceso.codigo_invitacion}</code> (vence en {config.telegram.invitacion_horas} h)\n"
        )
        if enlace:
            texto += f"Enlace directo: {enlace}\n"
        texto += "Compártelo por WhatsApp: con el código (/unirse CODIGO) o el enlace puede entrar al bot."
        ctx.cliente.send_message(ctx.chat_id, texto)


class ComandoFamilia(ComandoBase):
    nombre = "familia"
    descripcion = "Lista los accesos del vehículo"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        vehiculo = self.resolver_vehiculo_o_responder(ctx)
        if vehiculo is None:
            return
        accesos = list(vehiculo.accesos_telegram.filter(activo=True).order_by("-rol", "nombres"))
        es_propietario = self._accesos.puede_invitar(ctx.chat_id, vehiculo)
        texto = FormateadorMensajes().familia(accesos)
        teclado = self._teclados.familia(accesos, es_propietario) if es_propietario else None
        ctx.cliente.send_message(ctx.chat_id, texto, reply_markup=teclado or None)


class ComandoRevocar(ComandoBase):
    nombre = "revocar"
    descripcion = "Quita el acceso de un familiar (solo propietario): /revocar <nombre>"
    permiso = "invitar"

    def ejecutar(self, ctx) -> None:
        if not ctx.args:
            ctx.cliente.send_message(ctx.chat_id, "Uso: /revocar <nombre>")
            return
        nombre_buscado = " ".join(ctx.args)
        vehiculo = self.resolver_vehiculo_o_responder(ctx)
        if vehiculo is None:
            return
        acceso = vehiculo.accesos_telegram.filter(
            activo=True, rol="FAMILIAR", nombres__icontains=nombre_buscado,
        ).first()
        if acceso is None:
            ctx.cliente.send_message(ctx.chat_id, f"No se encontró a '{nombre_buscado}' entre los familiares.")
            return
        revocar_acceso(ctx, acceso)


def revocar_acceso(ctx, acceso) -> None:
    """Usado tanto por /revocar como por el botón 'Revocar' (callback ``rv``)."""
    acceso.activo = False
    acceso.save(update_fields=["activo"])
    if acceso.vinculado:
        ctx.cliente.send_message(acceso.chat_id, f"🚫 Tu acceso a {acceso.vehiculo.placa} fue revocado por el propietario.")
    ctx.cliente.send_message(ctx.chat_id, f"✅ Se revocó el acceso de {acceso.nombres}.")
