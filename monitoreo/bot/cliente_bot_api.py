"""Wrapper HTTP de la Bot API de Telegram, compartido por ``TelegramService``,
``DifusorAlertas`` y los comandos del bot (sección 4)."""
from __future__ import annotations

import logging
import time
from typing import Optional

import requests

from config import config

logger = logging.getLogger("monitoreo.telegram")

ESPERAS_REINTENTO_S = [1, 2, 5, 10]


class ClienteBotAPI:
    """Cliente de bajo nivel de la Bot API. Nunca lanza excepciones de red:
    siempre devuelve un dict con al menos la clave ``"ok"`` (igual que la
    respuesta real de Telegram), más ``"_status_code"`` para el llamador."""

    def __init__(self, token: Optional[str] = None, timeout: Optional[float] = None, reintentos: Optional[int] = None):
        self._token = token or config.telegram.bot_token
        self._timeout = timeout or config.telegram.timeout_s
        self._reintentos = reintentos if reintentos is not None else config.telegram.reintentos

    @property
    def _base_url(self) -> str:
        return f"{config.telegram.api_base}/bot{self._token}"

    def _llamar(self, metodo: str, http: str = "post", reintentable: bool = True, **kwargs) -> dict:
        intentos = self._reintentos if reintentable else 1
        ultimo_error = None
        for intento in range(1, intentos + 1):
            try:
                funcion = requests.post if http == "post" else requests.get
                resp = funcion(f"{self._base_url}/{metodo}", **kwargs)
                cuerpo = resp.json() if resp.content else {}
                cuerpo["_status_code"] = resp.status_code
                if cuerpo.get("ok") or not reintentable:
                    return cuerpo
                ultimo_error = cuerpo.get("description", cuerpo)
            except requests.RequestException as exc:
                ultimo_error = str(exc)
                logger.warning("Fallo de red llamando a %s (intento %s/%s): %s", metodo, intento, intentos, exc)
            except ValueError:
                ultimo_error = "respuesta no es JSON válido"

            if intento < intentos:
                time.sleep(ESPERAS_REINTENTO_S[min(intento - 1, len(ESPERAS_REINTENTO_S) - 1)])

        return {"ok": False, "description": str(ultimo_error), "_status_code": 0}

    # ------------------------------------------------------------------ #
    # Información y configuración del bot
    # ------------------------------------------------------------------ #
    def get_me(self) -> dict:
        return self._llamar("getMe", http="get", timeout=self._timeout)

    def delete_webhook(self) -> dict:
        return self._llamar("deleteWebhook", http="post", data={"drop_pending_updates": False}, timeout=self._timeout)

    def set_my_commands(self, comandos: list) -> dict:
        """``comandos``: lista de tuplas (comando_sin_barra, descripcion)."""
        import json

        cuerpo = [{"command": c, "description": d} for c, d in comandos]
        return self._llamar(
            "setMyCommands", http="post", reintentable=False,
            data={"commands": json.dumps(cuerpo, ensure_ascii=False)}, timeout=self._timeout,
        )

    # ------------------------------------------------------------------ #
    # Long polling
    # ------------------------------------------------------------------ #
    def get_updates(self, offset: Optional[int] = None, timeout: Optional[int] = None,
                     allowed_updates: Optional[list] = None) -> dict:
        import json

        timeout = timeout if timeout is not None else config.telegram.polling_timeout_s
        params = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        if allowed_updates is not None:
            params["allowed_updates"] = json.dumps(allowed_updates)
        return self._llamar(
            "getUpdates", http="get", reintentable=False, params=params, timeout=timeout + 10,
        )

    # ------------------------------------------------------------------ #
    # Envío de mensajes
    # ------------------------------------------------------------------ #
    def send_message(self, chat_id: str, text: str, reply_markup: Optional[dict] = None,
                      parse_mode: str = "HTML", disable_notification: bool = False) -> dict:
        """``disable_notification=True`` -> alerta silenciosa: el mensaje llega
        igual pero el teléfono no suena (tipos de riesgo MEDIO, sección 5.4)."""
        import json

        data = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        if disable_notification:
            data["disable_notification"] = True
        return self._llamar("sendMessage", data=data, timeout=self._timeout)

    def send_photo(self, chat_id: str, foto_bytes: bytes, caption: str = "",
                    reply_markup: Optional[dict] = None, parse_mode: str = "HTML",
                    disable_notification: bool = False) -> dict:
        import json

        data = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": parse_mode}
        if reply_markup is not None:
            data["reply_markup"] = json.dumps(reply_markup)
        if disable_notification:
            data["disable_notification"] = True
        return self._llamar(
            "sendPhoto", data=data, files={"photo": ("foto.jpg", foto_bytes, "image/jpeg")},
            timeout=config.telegram.foto_timeout_s + self._timeout,
        )

    def send_location(self, chat_id: str, lat: float, lon: float) -> dict:
        return self._llamar(
            "sendLocation", data={"chat_id": chat_id, "latitude": lat, "longitude": lon}, timeout=self._timeout,
        )

    def send_chat_action(self, chat_id: str, action: str = "upload_photo") -> dict:
        return self._llamar(
            "sendChatAction", reintentable=False, data={"chat_id": chat_id, "action": action}, timeout=self._timeout,
        )

    # ------------------------------------------------------------------ #
    # Edición / callbacks
    # ------------------------------------------------------------------ #
    def answer_callback_query(self, callback_query_id: str, text: str = "", show_alert: bool = False) -> dict:
        return self._llamar(
            "answerCallbackQuery", reintentable=False,
            data={"callback_query_id": callback_query_id, "text": text[:200], "show_alert": show_alert},
            timeout=self._timeout,
        )

    def edit_message_reply_markup(self, chat_id: str, message_id: int, reply_markup: dict) -> dict:
        import json

        return self._llamar(
            "editMessageReplyMarkup", reintentable=False,
            data={"chat_id": chat_id, "message_id": message_id, "reply_markup": json.dumps(reply_markup)},
            timeout=self._timeout,
        )

    def delete_message(self, chat_id: str, message_id: int) -> dict:
        return self._llamar(
            "deleteMessage", reintentable=False, data={"chat_id": chat_id, "message_id": message_id},
            timeout=self._timeout,
        )
