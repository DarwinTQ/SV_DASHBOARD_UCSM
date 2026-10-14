"""Bot de Telegram multiusuario: long polling (sección 4). Lo arranca
``main.py`` cuando hay token y ``TELEGRAM_BOT_COMANDOS`` está activo."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor

from django.db import close_old_connections

from config import config
from monitoreo.bot.autorizacion import ServicioAccesos
from monitoreo.bot.cliente_bot_api import ClienteBotAPI
from monitoreo.bot.comandos.registro import RegistroComandos
from monitoreo.bot.contexto import ContextoComando
from monitoreo.bot.estado_bot import EstadoBot
from monitoreo.bot.limitador import LimitadorFrecuencia
from monitoreo.bot.manejador_callbacks import ManejadorCallbacks
from monitoreo.bot.registro_metricas import RegistroComandosBot
from monitoreo.services.reloj_service import RelojServidor

from .base_worker import WorkerBase

ESPERAS_ERROR_S = [1, 2, 5, 10]


class BotTelegramWorker(WorkerBase):
    """Un hilo hace ``getUpdates``; cada update se procesa en un pool de 3
    hilos (una foto lenta no bloquea a los demás usuarios)."""

    nombre = "BotTelegram"

    def __init__(self):
        super().__init__()
        self._cliente = ClienteBotAPI()
        self._registro_comandos = RegistroComandos()
        self._manejador_callbacks = ManejadorCallbacks(self._registro_comandos)
        self._accesos = ServicioAccesos()
        self._limitador = LimitadorFrecuencia.instancia()
        self._offset = None
        self._pool = ThreadPoolExecutor(max_workers=3)
        self._errores_consecutivos = 0
        self.usuario_bot = ""

    def disponible(self) -> bool:
        return bool(config.telegram.bot_token) and config.telegram.comandos_activos

    def iniciar(self) -> None:
        if not self.disponible():
            self.logger.info("Bot de Telegram desactivado (sin token o TELEGRAM_BOT_COMANDOS=False).")
            return
        if not self._preparar():
            return
        super().iniciar()

    def _preparar(self) -> bool:
        self._cliente.delete_webhook()
        info = self._cliente.get_me()
        if not info.get("ok"):
            self.logger.error("No se pudo conectar con la Bot API de Telegram: %s", info.get("description"))
            return False
        self.usuario_bot = (info.get("result") or {}).get("username", "")
        self._cliente.set_my_commands(self._registro_comandos.lista_para_set_my_commands())
        EstadoBot.instancia().marcar_conectado(self.usuario_bot)
        self.logger.info("Bot @%s conectado (long polling).", self.usuario_bot)
        return True

    def detener(self) -> None:
        EstadoBot.instancia().marcar_desconectado()
        super().detener()
        self._pool.shutdown(wait=False)

    # ------------------------------------------------------------------ #
    # Bucle principal
    # ------------------------------------------------------------------ #
    def ciclo(self) -> None:
        respuesta = self._cliente.get_updates(
            offset=self._offset, timeout=config.telegram.polling_timeout_s,
            allowed_updates=["message", "callback_query"],
        )
        if not respuesta.get("ok"):
            self._errores_consecutivos += 1
            espera = ESPERAS_ERROR_S[min(self._errores_consecutivos - 1, len(ESPERAS_ERROR_S) - 1)]
            self.logger.warning("Fallo en getUpdates: %s (reintentando en %ss)", respuesta.get("description"), espera)
            time.sleep(espera)
            return
        self._errores_consecutivos = 0

        for update in respuesta.get("result", []):
            self._offset = update["update_id"] + 1
            self._pool.submit(self._procesar_update_seguro, update)

    def _procesar_update_seguro(self, update: dict) -> None:
        try:
            self._procesar_update(update)
        except Exception:
            self.logger.exception("Error procesando update %s", update.get("update_id"))
        finally:
            close_old_connections()

    def _procesar_update(self, update: dict) -> None:
        t_recepcion = RelojServidor.ahora()
        EstadoBot.instancia().registrar_update()
        if "callback_query" in update:
            self._procesar_callback(update["callback_query"], t_recepcion)
        elif "message" in update:
            self._procesar_mensaje(update["message"], t_recepcion)

    def _construir_contexto(self, chat_id: str, usuario: dict, t_recepcion) -> ContextoComando:
        accesos = [] if self._accesos.es_admin(chat_id) else list(self._accesos.accesos_de(chat_id))
        return ContextoComando(
            chat_id=chat_id, cliente=self._cliente, accesos=accesos,
            nombre_telegram=usuario.get("first_name", ""), usuario_telegram=usuario.get("username", ""),
            t_recepcion=t_recepcion,
        )

    def _procesar_mensaje(self, mensaje: dict, t_recepcion) -> None:
        chat = mensaje.get("chat") or {}
        chat_id = str(chat.get("id"))
        texto = (mensaje.get("text") or "").strip()
        if not chat_id or not texto:
            return

        if not self._limitador.permitir_comando(chat_id):
            self._cliente.send_message(chat_id, "⏳ Muchos comandos seguidos, espera un momento.")
            return

        comando, args = self._registro_comandos.resolver_desde_texto(texto)
        ctx = self._construir_contexto(chat_id, mensaje.get("from") or {}, t_recepcion)
        ctx.texto = texto
        ctx.args = args
        ctx.mensaje_id_origen = mensaje.get("message_id")

        if comando is None:
            self._responder_no_reconocido(ctx)
            self._registrar_comando(ctx, "desconocido", exito=False, t_recepcion=t_recepcion)
            return

        if comando.requiere_vinculacion and not ctx.esta_vinculado():
            self._cliente.send_message(chat_id, "Primero vincúlate con /vincular o /unirse. Usa /ayuda para más información.")
            self._registrar_comando(ctx, comando.nombre, exito=False, t_recepcion=t_recepcion)
            return

        exito = True
        try:
            comando.ejecutar(ctx)
        except Exception:
            self.logger.exception("Error ejecutando comando '%s'", comando.nombre)
            exito = False
            self._cliente.send_message(chat_id, "⚠️ Ocurrió un error procesando tu solicitud.")
        self._registrar_comando(ctx, comando.nombre, exito=exito, t_recepcion=t_recepcion)

    def _responder_no_reconocido(self, ctx: ContextoComando) -> None:
        from monitoreo.bot.formateador import FormateadorMensajes
        from monitoreo.bot.teclados import FabricaTeclados

        if not ctx.esta_vinculado():
            self._cliente.send_message(ctx.chat_id, FormateadorMensajes().bienvenida(False))
            return
        es_propietario = any(a.rol == "PROPIETARIO" for a in ctx.accesos) or ctx.es_admin()
        puede_controlar = any(a.puede_controlar for a in ctx.accesos) or ctx.es_admin()
        self._cliente.send_message(
            ctx.chat_id, "No entendí ese comando.\n\n" + FormateadorMensajes().ayuda(es_propietario, puede_controlar, True),
            reply_markup=FabricaTeclados().menu_principal(puede_controlar, es_propietario),
        )

    def _procesar_callback(self, callback: dict, t_recepcion) -> None:
        chat = (callback.get("message") or {}).get("chat") or {}
        chat_id = str(chat.get("id"))
        if not chat_id:
            return

        ctx = self._construir_contexto(chat_id, callback.get("from") or {}, t_recepcion)
        ctx.es_callback = True
        ctx.callback_query_id = callback.get("id")
        ctx.mensaje_id_origen = (callback.get("message") or {}).get("message_id")
        datos = callback.get("data", "")

        if not self._limitador.permitir_comando(chat_id):
            self._cliente.answer_callback_query(ctx.callback_query_id, "Espera un momento…")
            return

        self._manejador_callbacks.procesar(ctx, datos)
        self._registrar_comando(ctx, f"callback:{datos.split(':')[0]}", exito=True, t_recepcion=t_recepcion)

    def _registrar_comando(self, ctx: ContextoComando, nombre_comando: str, exito: bool, t_recepcion) -> None:
        tiempo_ms = (RelojServidor.ahora() - t_recepcion).total_seconds() * 1000
        rol = "ADMIN" if ctx.es_admin() else (ctx.accesos[0].rol if ctx.accesos else "SIN_VINCULAR")
        RegistroComandosBot.instancia().registrar(
            chat_id=ctx.chat_id, rol=rol, comando=nombre_comando, exito=exito, tiempo_respuesta_ms=tiempo_ms,
        )
