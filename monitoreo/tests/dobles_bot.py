"""Dobles de prueba del bot de Telegram: nunca llaman a la red real."""
from __future__ import annotations


class ClienteBotAPIFalso:
    """Implementa la misma interfaz que ``ClienteBotAPI`` pero solo registra
    las llamadas en memoria, para usarla en pruebas."""

    def __init__(self):
        self.mensajes_enviados = []
        self.fotos_enviadas = []
        self.ubicaciones_enviadas = []
        self.callbacks_respondidos = []
        self.markups_editados = []
        self.mensajes_borrados = []
        self._contador_mensaje_id = 1000

    def get_me(self):
        return {"ok": True, "result": {"username": "bot_prueba"}, "_status_code": 200}

    def delete_webhook(self):
        return {"ok": True, "_status_code": 200}

    def set_my_commands(self, comandos):
        return {"ok": True, "_status_code": 200}

    def get_updates(self, offset=None, timeout=None, allowed_updates=None):
        return {"ok": True, "result": [], "_status_code": 200}

    def send_message(self, chat_id, text, reply_markup=None, parse_mode="HTML"):
        self._contador_mensaje_id += 1
        self.mensajes_enviados.append({"chat_id": chat_id, "texto": text, "reply_markup": reply_markup})
        return {"ok": True, "result": {"message_id": self._contador_mensaje_id}, "_status_code": 200}

    def send_photo(self, chat_id, foto_bytes, caption="", reply_markup=None, parse_mode="HTML"):
        self._contador_mensaje_id += 1
        self.fotos_enviadas.append({"chat_id": chat_id, "caption": caption, "reply_markup": reply_markup})
        return {"ok": True, "result": {"message_id": self._contador_mensaje_id}, "_status_code": 200}

    def send_location(self, chat_id, lat, lon):
        self.ubicaciones_enviadas.append({"chat_id": chat_id, "lat": lat, "lon": lon})
        return {"ok": True, "result": {}, "_status_code": 200}

    def send_chat_action(self, chat_id, action="upload_photo"):
        return {"ok": True, "_status_code": 200}

    def answer_callback_query(self, callback_query_id, text="", show_alert=False):
        self.callbacks_respondidos.append({"id": callback_query_id, "texto": text, "alerta": show_alert})
        return {"ok": True, "_status_code": 200}

    def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
        self.markups_editados.append({"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup})
        return {"ok": True, "_status_code": 200}

    def delete_message(self, chat_id, message_id):
        self.mensajes_borrados.append({"chat_id": chat_id, "message_id": message_id})
        return {"ok": True, "_status_code": 200}
