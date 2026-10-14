"""/vincular, /unirse, /salir."""
from __future__ import annotations

import re

from django.utils import timezone

from monitoreo.bot.formateador import FormateadorMensajes
from monitoreo.bot.invitaciones import ServicioInvitaciones
from monitoreo.bot.limitador import LimitadorFrecuencia

from .base import ComandoBase


def _candidatos_telefono(crudo: str) -> list:
    limpio = re.sub(r"[^\d+]", "", crudo or "")
    candidatos = {limpio}
    if limpio.startswith("+51") and len(limpio) > 3:
        candidatos.add(limpio[3:])
    elif limpio.startswith("51") and len(limpio) > 9:
        candidatos.add("+" + limpio)
        candidatos.add(limpio[2:])
    elif limpio and not limpio.startswith("+"):
        candidatos.add("+51" + limpio)
    return [c for c in candidatos if c]


class ComandoVincular(ComandoBase):
    nombre = "vincular"
    descripcion = "Vincúlate como propietario: /vincular DNI TELEFONO"
    requiere_vinculacion = False
    permiso = None

    def ejecutar(self, ctx) -> None:
        limitador = LimitadorFrecuencia.instancia()
        if not limitador.puede_intentar_vinculacion(ctx.chat_id):
            ctx.cliente.send_message(ctx.chat_id, "⛔ Demasiados intentos. Espera una hora e inténtalo de nuevo.")
            return

        self._borrar_mensaje_origen(ctx)

        if len(ctx.args) < 2:
            ctx.cliente.send_message(ctx.chat_id, "Uso: /vincular DNI TELEFONO")
            return

        dni, telefono = ctx.args[0], ctx.args[1]
        from monitoreo.models import Conductor

        conductor = Conductor.objects.filter(dni=dni, telefono__in=_candidatos_telefono(telefono)).first()
        if conductor is None:
            intentos = limitador.registrar_intento_vinculacion_fallido(ctx.chat_id)
            ctx.cliente.send_message(
                ctx.chat_id,
                f"❌ No encontramos un conductor con esos datos (intento {intentos}/5 por hora).",
            )
            return

        vehiculos = list(conductor.vehiculos.all())
        if not vehiculos:
            ctx.cliente.send_message(ctx.chat_id, "⚠️ Ese conductor no tiene vehículos registrados todavía.")
            return

        from monitoreo.models import AccesoTelegram

        placas = []
        for vehiculo in vehiculos:
            AccesoTelegram.objects.update_or_create(
                vehiculo=vehiculo, chat_id=ctx.chat_id,
                defaults={
                    "nombres": conductor.nombre_completo, "rol": "PROPIETARIO",
                    "puede_controlar": True, "recibe_alertas": True, "activo": True,
                    "fecha_vinculacion": timezone.now(),
                },
            )
            placas.append(vehiculo.placa)

        conductor.telegram_chat_id = ctx.chat_id
        conductor.save(update_fields=["telegram_chat_id"])
        limitador.reiniciar_intentos_vinculacion(ctx.chat_id)

        puede_invitar = True
        teclado = self._teclados.menu_principal(True, puede_invitar)
        ctx.cliente.send_message(
            ctx.chat_id,
            f"✅ Vinculado como propietario de: {', '.join(placas)}.\nUsa /invitar para dar acceso a tu familia.",
            reply_markup=teclado,
        )

    def _borrar_mensaje_origen(self, ctx) -> None:
        """El mensaje con el DNI no debe quedar visible en el chat."""
        if ctx.mensaje_id_origen:
            ctx.cliente.delete_message(ctx.chat_id, ctx.mensaje_id_origen)


class ComandoUnirse(ComandoBase):
    nombre = "unirse"
    descripcion = "Vincúlate como familiar invitado: /unirse CODIGO"
    requiere_vinculacion = False
    permiso = None

    def ejecutar(self, ctx) -> None:
        if not ctx.args:
            ctx.cliente.send_message(ctx.chat_id, "Uso: /unirse CODIGO")
            return

        acceso, error = ServicioInvitaciones().canjear(ctx.args[0], ctx.chat_id, ctx.usuario_telegram)
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


class ComandoSalir(ComandoBase):
    nombre = "salir"
    descripcion = "Quita tu propio acceso a un vehículo"
    permiso = None

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        vehiculo = self.resolver_vehiculo_o_responder(ctx, placa)
        if vehiculo is None:
            return
        acceso = self._accesos.acceso_para_vehiculo(ctx.chat_id, vehiculo)
        if acceso is None:
            ctx.cliente.send_message(ctx.chat_id, "No tienes un acceso vinculado a ese vehículo.")
            return
        acceso.activo = False
        acceso.save(update_fields=["activo"])
        ctx.cliente.send_message(ctx.chat_id, f"🚪 Saliste del acceso a {vehiculo.placa}.")
