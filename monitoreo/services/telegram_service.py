"""Envíos puntuales de Telegram: prueba desde Configuración y reintento de
una alerta concreta (la difusión a todos los destinatarios la hace
``DifusorAlertas``). Comparte ``ClienteBotAPI`` con el bot y el difusor."""
from __future__ import annotations

import logging
from typing import Optional

from django.utils import timezone

from config import config
from monitoreo.bot.cliente_bot_api import ClienteBotAPI
from monitoreo.bot.autorizacion import ServicioAccesos
from monitoreo.bot.teclados import FabricaTeclados
from monitoreo.models import AlertaTelegram

logger = logging.getLogger("monitoreo.telegram")


class TelegramService:
    """Operaciones de un solo destinatario: mensaje de prueba y reintento."""

    def __init__(self, cliente_bot: Optional[ClienteBotAPI] = None):
        self._cliente = cliente_bot or ClienteBotAPI()
        self._accesos = ServicioAccesos()
        self._teclados = FabricaTeclados()

    def enviar_prueba(self, chat_id: str) -> dict:
        resp = self._cliente.send_message(chat_id, "✅ Mensaje de prueba — Sistema de Monitoreo Vehicular UCSM.")
        return {"status": resp.get("_status_code", 0), "cuerpo": resp}

    def reintentar(self, alerta: AlertaTelegram) -> AlertaTelegram:
        """Reenvía una alerta FALLIDA a su mismo destinatario (``alerta.chat_id``)."""
        incidencia = alerta.incidencia
        imagen_campo = incidencia.imagen_anotada or incidencia.imagen
        puede_controlar = self._accesos.puede_controlar(alerta.chat_id, incidencia.dispositivo.vehiculo)
        teclado = self._teclados.alerta(incidencia, puede_controlar) if incidencia.validacion is None else None

        if imagen_campo:
            with imagen_campo.open("rb") as archivo:
                resp = self._cliente.send_photo(alerta.chat_id, archivo.read(), caption=alerta.mensaje, reply_markup=teclado)
        else:
            resp = self._cliente.send_message(alerta.chat_id, alerta.mensaje, reply_markup=teclado)

        alerta.intentos += 1
        alerta.codigo_http = resp.get("_status_code")
        if resp.get("ok"):
            alerta.estado_envio = "ENVIADO"
            alerta.message_id = (resp.get("result") or {}).get("message_id")
            alerta.fecha_envio = timezone.now()
            alerta.error = ""
        else:
            alerta.estado_envio = "FALLIDO"
            alerta.error = str(resp.get("description", resp))[:500]
        alerta.save()
        return alerta
