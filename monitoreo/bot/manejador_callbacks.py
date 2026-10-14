"""Procesa los botones en línea (callback_query) de la sección 7."""
from __future__ import annotations

import logging

from monitoreo.bot.autorizacion import ServicioAccesos
from monitoreo.bot.teclados import FabricaTeclados

logger = logging.getLogger("monitoreo.bot")


class ManejadorCallbacks:
    """``callback_data`` cortos y validados: v / f / u / d / ok / rv / pc / sel."""

    def __init__(self, registro):
        self._registro = registro
        self._accesos = ServicioAccesos()
        self._teclados = FabricaTeclados()

    def procesar(self, ctx, callback_data: str) -> None:
        partes = (callback_data or "").split(":")
        prefijo = partes[0] if partes else ""
        try:
            manejador = {
                "v": self._validar, "f": self._foto, "u": self._ubicacion, "d": self._desarmar,
                "ok": self._control_confirmado, "rv": self._revocar, "pc": self._permiso_control,
                "sel": self._seleccion_vehiculo,
            }.get(prefijo)
            if manejador is None:
                ctx.cliente.answer_callback_query(ctx.callback_query_id)
                return
            manejador(ctx, partes)
        except Exception:
            logger.exception("Error procesando callback '%s'", callback_data)
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "Ocurrió un error, intenta de nuevo.")

    # ------------------------------------------------------------------ #
    def _nombre_para(self, ctx, vehiculo) -> str:
        if ctx.es_admin():
            return "un administrador"
        acceso = next((a for a in ctx.accesos if a.vehiculo_id == vehiculo.pk), None)
        return acceso.nombres if acceso else "alguien"

    def _validar(self, ctx, partes) -> None:
        from django.utils import timezone

        from monitoreo.models import AlertaTelegram, Incidencia
        from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal

        id_incidencia, codigo = int(partes[1]), partes[2]
        incidencia = Incidencia.objects.select_related("dispositivo__vehiculo").filter(pk=id_incidencia).first()
        if incidencia is None:
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "Incidencia no encontrada.")
            return
        if not self._accesos.puede_consultar(ctx.chat_id, incidencia.dispositivo.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes acceso a esta incidencia.", show_alert=True)
            return
        if incidencia.validacion is not None:
            ctx.cliente.answer_callback_query(
                ctx.callback_query_id, "Esta incidencia ya fue validada por otra persona.", show_alert=True,
            )
            return

        quien = self._nombre_para(ctx, incidencia.dispositivo.vehiculo)
        incidencia.validacion = codigo
        incidencia.estado = "ATENDIDA"
        incidencia.save(update_fields=["validacion", "estado"])

        hora = timezone.localtime().strftime("%H:%M")
        etiqueta = "real (VP)" if codigo == "VP" else "falsa alarma (FP)"
        texto_boton = f"✔ Validada como {etiqueta} por {quien} a las {hora}"

        for alerta in AlertaTelegram.objects.filter(incidencia=incidencia).exclude(message_id__isnull=True):
            ctx.cliente.edit_message_reply_markup(alerta.chat_id, alerta.message_id, self._teclados.validada(texto_boton))

        NotificadorTiempoReal.incidencia_actualizada({
            "id": incidencia.pk, "estado": incidencia.estado, "validacion": incidencia.validacion,
        })
        ctx.cliente.answer_callback_query(ctx.callback_query_id, "¡Gracias! Validación registrada.")

    def _foto(self, ctx, partes) -> None:
        from monitoreo.bot.comandos.foto import ComandoFoto
        from monitoreo.models import Dispositivo

        dispositivo = Dispositivo.objects.select_related("vehiculo").filter(pk=int(partes[1])).first()
        if dispositivo is None or not self._accesos.puede_consultar(ctx.chat_id, dispositivo.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes acceso.", show_alert=True)
            return
        ctx.cliente.answer_callback_query(ctx.callback_query_id, "📷 Capturando…")
        ComandoFoto().responder_dispositivo(ctx, dispositivo)

    def _ubicacion(self, ctx, partes) -> None:
        from monitoreo.bot.comandos.ubicacion import ComandoUbicacion
        from monitoreo.models import Dispositivo

        dispositivo = Dispositivo.objects.select_related("vehiculo").filter(pk=int(partes[1])).first()
        if dispositivo is None or not self._accesos.puede_consultar(ctx.chat_id, dispositivo.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes acceso.", show_alert=True)
            return
        ctx.cliente.answer_callback_query(ctx.callback_query_id)
        ComandoUbicacion().responder_dispositivo(ctx, dispositivo)

    def _desarmar(self, ctx, partes) -> None:
        from monitoreo.bot.comandos.control import aplicar_control
        from monitoreo.models import Dispositivo

        dispositivo = Dispositivo.objects.select_related("vehiculo").filter(pk=int(partes[1])).first()
        if dispositivo is None or not self._accesos.puede_controlar(ctx.chat_id, dispositivo.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes permiso para desarmar.", show_alert=True)
            return
        if not dispositivo.armado:
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "Ya estaba desarmado.")
            return
        aplicar_control(ctx, dispositivo, armar=False)
        ctx.cliente.answer_callback_query(ctx.callback_query_id, "🔓 Desarmado.")

    def _control_confirmado(self, ctx, partes) -> None:
        accion = partes[1] if len(partes) > 1 else "info"
        if accion in ("info", "noop"):
            ctx.cliente.answer_callback_query(ctx.callback_query_id)
            return
        if accion == "cancelar":
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "Cancelado.")
            return

        from monitoreo.bot.comandos.control import aplicar_control
        from monitoreo.models import Dispositivo

        dispositivo = Dispositivo.objects.select_related("vehiculo").filter(pk=int(partes[2])).first()
        if dispositivo is None or not self._accesos.puede_controlar(ctx.chat_id, dispositivo.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes permiso.", show_alert=True)
            return
        aplicar_control(ctx, dispositivo, armar=(accion == "armar"))
        ctx.cliente.answer_callback_query(ctx.callback_query_id, "✅ Hecho.")

    def _revocar(self, ctx, partes) -> None:
        from monitoreo.bot.comandos.familia import revocar_acceso
        from monitoreo.models import AccesoTelegram

        acceso = AccesoTelegram.objects.select_related("vehiculo").filter(pk=int(partes[1])).first()
        if acceso is None or not self._accesos.puede_invitar(ctx.chat_id, acceso.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes permiso.", show_alert=True)
            return
        revocar_acceso(ctx, acceso)
        ctx.cliente.answer_callback_query(ctx.callback_query_id, "Acceso revocado.")

    def _permiso_control(self, ctx, partes) -> None:
        from monitoreo.models import AccesoTelegram

        acceso = AccesoTelegram.objects.select_related("vehiculo").filter(pk=int(partes[1])).first()
        if acceso is None or not self._accesos.puede_invitar(ctx.chat_id, acceso.vehiculo):
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "No tienes permiso.", show_alert=True)
            return
        acceso.puede_controlar = not acceso.puede_controlar
        acceso.save(update_fields=["puede_controlar"])
        if acceso.vinculado:
            estado = "ahora puedes" if acceso.puede_controlar else "ya no puedes"
            ctx.cliente.send_message(acceso.chat_id, f"🔑 {estado} armar/desarmar {acceso.vehiculo.placa}.")
        ctx.cliente.answer_callback_query(ctx.callback_query_id, "Permiso actualizado.")

    def _seleccion_vehiculo(self, ctx, partes) -> None:
        from monitoreo.models import Vehiculo

        nombre_comando, id_vehiculo = partes[1], int(partes[2])
        vehiculo = Vehiculo.objects.filter(pk=id_vehiculo).first()
        if vehiculo is None:
            ctx.cliente.answer_callback_query(ctx.callback_query_id, "Vehículo no encontrado.")
            return
        comando = self._registro.obtener(nombre_comando)
        if comando is None:
            ctx.cliente.answer_callback_query(ctx.callback_query_id)
            return
        ctx.vehiculo = vehiculo
        ctx.args = []
        ctx.cliente.answer_callback_query(ctx.callback_query_id)
        comando.ejecutar(ctx)
