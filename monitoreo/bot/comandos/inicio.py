"""/start, /ayuda."""
from __future__ import annotations

from monitoreo.bot.formateador import FormateadorMensajes
from monitoreo.bot.invitaciones import ServicioInvitaciones

from .base import ComandoBase


class ComandoInicio(ComandoBase):
    nombre = "start"
    descripcion = "Bienvenida y vinculación por enlace"
    requiere_vinculacion = False
    permiso = None

    def ejecutar(self, ctx) -> None:
        if ctx.args:
            self._canjear(ctx, ctx.args[0])
            return

        vinculado = ctx.esta_vinculado()
        teclado = None
        if vinculado:
            puede_controlar = any(a.puede_controlar for a in ctx.accesos) or ctx.es_admin()
            puede_invitar = any(a.rol == "PROPIETARIO" for a in ctx.accesos) or ctx.es_admin()
            teclado = self._teclados.menu_principal(puede_controlar, puede_invitar)
        ctx.cliente.send_message(
            ctx.chat_id, FormateadorMensajes().bienvenida(vinculado, ctx.nombre_telegram), reply_markup=teclado,
        )

    def _canjear(self, ctx, codigo: str) -> None:
        acceso, error = ServicioInvitaciones().canjear(codigo, ctx.chat_id, ctx.usuario_telegram)
        if error:
            ctx.cliente.send_message(ctx.chat_id, f"⚠️ {error}")
            return
        self._avisar_propietario(ctx, acceso)
        ctx.cliente.send_message(
            ctx.chat_id, f"✅ Listo, {acceso.nombres}. Ya tienes acceso a <b>{acceso.vehiculo.placa}</b>.",
        )

    def _avisar_propietario(self, ctx, acceso) -> None:
        propietario = (
            acceso.vehiculo.accesos_telegram.filter(rol="PROPIETARIO", activo=True).exclude(chat_id="").first()
        )
        if not propietario:
            return
        parentesco = f" ({acceso.parentesco})" if acceso.parentesco else ""
        ctx.cliente.send_message(
            propietario.chat_id, f"👪 {acceso.nombres}{parentesco} ya tiene acceso a {acceso.vehiculo.placa}.",
        )


class ComandoAyuda(ComandoBase):
    nombre = "ayuda"
    descripcion = "Lista de comandos disponibles"
    requiere_vinculacion = False
    permiso = None

    def ejecutar(self, ctx) -> None:
        vinculado = ctx.esta_vinculado()
        es_propietario = any(a.rol == "PROPIETARIO" for a in ctx.accesos) or ctx.es_admin()
        puede_controlar = any(a.puede_controlar for a in ctx.accesos) or ctx.es_admin()
        ctx.cliente.send_message(ctx.chat_id, FormateadorMensajes().ayuda(es_propietario, puede_controlar, vinculado))
