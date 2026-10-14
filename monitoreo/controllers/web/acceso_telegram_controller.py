"""Gestión web de accesos de Telegram: invitar, revocar, permisos (sección 8)."""
from __future__ import annotations

import base64
import io

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import View

from monitoreo.bot.cliente_bot_api import ClienteBotAPI
from monitoreo.bot.invitaciones import ServicioInvitaciones
from monitoreo.models import AccesoTelegram, Vehiculo


class AccesoTelegramInvitarController(LoginRequiredMixin, View):
    """POST /vehiculos/<id>/invitar-telegram/ — crea la invitación y devuelve
    código, enlace y un QR en base64 para compartir por WhatsApp."""

    def post(self, request, pk):
        vehiculo = get_object_or_404(Vehiculo, pk=pk)
        nombres = request.POST.get("nombres", "").strip()
        parentesco = request.POST.get("parentesco", "").strip()
        if not nombres:
            return JsonResponse({"ok": False, "error": "El nombre es obligatorio."}, status=400)

        acceso, error = ServicioInvitaciones().crear_invitacion(vehiculo, nombres, parentesco)
        if error:
            return JsonResponse({"ok": False, "error": error}, status=400)

        usuario_bot = self._usuario_bot()
        enlace = ServicioInvitaciones().enlace_invitacion(acceso, usuario_bot) if usuario_bot else ""
        qr_base64 = self._generar_qr(enlace) if enlace else None

        return JsonResponse({
            "ok": True, "codigo": acceso.codigo_invitacion, "enlace": enlace,
            "qr_base64": qr_base64, "nombres": acceso.nombres,
        })

    @staticmethod
    def _usuario_bot() -> str:
        info = ClienteBotAPI().get_me()
        return (info.get("result") or {}).get("username", "") if info.get("ok") else ""

    @staticmethod
    def _generar_qr(texto: str) -> str:
        import qrcode

        imagen = qrcode.make(texto)
        buffer = io.BytesIO()
        imagen.save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue()).decode("ascii")


class AccesoTelegramRevocarController(LoginRequiredMixin, View):
    """POST /accesos-telegram/<id>/revocar/"""

    def post(self, request, pk):
        acceso = get_object_or_404(AccesoTelegram, pk=pk)
        acceso.activo = False
        acceso.save(update_fields=["activo"])
        if acceso.vinculado:
            ClienteBotAPI().send_message(
                acceso.chat_id, f"🚫 Tu acceso a {acceso.vehiculo.placa} fue revocado por el propietario.",
            )
        messages.success(request, f"Se revocó el acceso de {acceso.nombres}.")
        return redirect("monitoreo:vehiculo_detalle", pk=acceso.vehiculo_id)


class AccesoTelegramPermisoController(LoginRequiredMixin, View):
    """POST /accesos-telegram/<id>/permiso/ — alterna puede_controlar."""

    def post(self, request, pk):
        acceso = get_object_or_404(AccesoTelegram, pk=pk)
        acceso.puede_controlar = not acceso.puede_controlar
        acceso.save(update_fields=["puede_controlar"])
        if acceso.vinculado:
            estado = "ahora puedes" if acceso.puede_controlar else "ya no puedes"
            ClienteBotAPI().send_message(acceso.chat_id, f"🔑 {estado} armar/desarmar {acceso.vehiculo.placa}.")
        messages.success(request, "Permiso actualizado.")
        return redirect("monitoreo:vehiculo_detalle", pk=acceso.vehiculo_id)
