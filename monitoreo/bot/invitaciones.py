"""Códigos de invitación para vincular familiares al bot (sección 5)."""
from __future__ import annotations

import secrets
import string
from typing import Optional

from django.utils import timezone

from config import config
from monitoreo.models import AccesoTelegram, Vehiculo

ALFABETO_CODIGO = "".join(c for c in (string.ascii_uppercase + string.digits) if c not in "0O1I")
LARGO_CODIGO = 6


class ServicioInvitaciones:
    """Genera, valida y canjea los códigos de invitación de familiares."""

    def generar_codigo(self) -> str:
        for _ in range(20):
            codigo = "".join(secrets.choice(ALFABETO_CODIGO) for _ in range(LARGO_CODIGO))
            if not AccesoTelegram.objects.filter(codigo_invitacion=codigo).exists():
                return codigo
        raise RuntimeError("No se pudo generar un código de invitación único.")

    def crear_invitacion(self, vehiculo: Vehiculo, nombres: str, parentesco: str = "") -> tuple:
        """Devuelve ``(acceso, error)``. ``error`` no es ``None`` si se superó
        ``TELEGRAM_MAX_ACCESOS_POR_VEHICULO``."""
        activos = AccesoTelegram.objects.filter(vehiculo=vehiculo, activo=True).count()
        if activos >= config.telegram.max_accesos_por_vehiculo:
            return None, (
                f"Este vehículo ya tiene {activos} accesos registrados "
                f"(máximo {config.telegram.max_accesos_por_vehiculo})."
            )

        acceso = AccesoTelegram.objects.create(
            vehiculo=vehiculo, nombres=nombres.strip(), parentesco=parentesco.strip(),
            rol="FAMILIAR", puede_controlar=False, recibe_alertas=True,
            codigo_invitacion=self.generar_codigo(),
            invitacion_expira=timezone.now() + timezone.timedelta(hours=config.telegram.invitacion_horas),
        )
        return acceso, None

    def enlace_invitacion(self, acceso: AccesoTelegram, usuario_bot: str) -> str:
        return f"https://t.me/{usuario_bot}?start={acceso.codigo_invitacion}"

    def canjear(self, codigo: str, chat_id: str, usuario_telegram: str = "") -> tuple:
        """Devuelve ``(acceso, error)``."""
        codigo = (codigo or "").strip().upper()
        acceso = AccesoTelegram.objects.filter(codigo_invitacion=codigo).select_related("vehiculo").first()
        if acceso is None:
            return None, "Código de invitación inválido o ya utilizado."
        if acceso.invitacion_vencida:
            return None, "Este código de invitación venció. Pide uno nuevo al propietario."
        if acceso.vinculado:
            return None, "Este código ya fue utilizado."

        acceso.chat_id = chat_id
        acceso.usuario_telegram = usuario_telegram or acceso.usuario_telegram
        acceso.fecha_vinculacion = timezone.now()
        acceso.codigo_invitacion = None
        acceso.invitacion_expira = None
        acceso.save(update_fields=[
            "chat_id", "usuario_telegram", "fecha_vinculacion", "codigo_invitacion", "invitacion_expira",
        ])
        return acceso, None
