"""Envía la alerta de una incidencia a TODOS los destinatarios autorizados
del vehículo (propietario + familiares con alertas activadas + admins),
en paralelo (sección 7)."""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from threading import Lock
from typing import Optional

from django.db import close_old_connections
from django.utils import timezone

from config import config
from monitoreo.bot.cliente_bot_api import ClienteBotAPI
from monitoreo.bot.formateador import FormateadorMensajes
from monitoreo.bot.teclados import FabricaTeclados
from monitoreo.models import AccesoTelegram, AlertaTelegram, Incidencia

logger = logging.getLogger("monitoreo.telegram")

ESPERAS_REINTENTO_S = [0.5, 1, 2]

# SQLite es de un solo escritor: serializa las escrituras (rápidas) de cada
# hilo destinatario para evitar "database table is locked"; los envíos HTTP
# (lo lento) siguen en paralelo, que es lo que realmente importa.
_LOCK_ESCRITURA_BD = Lock()


@dataclass
class Destinatario:
    chat_id: str
    puede_controlar: bool
    rol: str
    nombre: str


@dataclass
class ResultadoDifusion:
    alertas: list = field(default_factory=list)
    t_primera_confirmacion: Optional[object] = None
    exito: bool = False


class DifusorAlertas:
    """Crea una fila ``AlertaTelegram`` por destinatario y las envía en
    paralelo, para que el segundo familiar no espere al primero."""

    def __init__(self, cliente_bot: Optional[ClienteBotAPI] = None, max_hilos: int = 6):
        self._cliente = cliente_bot or ClienteBotAPI()
        self._max_hilos = max_hilos
        self._formateador = FormateadorMensajes()
        self._teclados = FabricaTeclados()

    def difundir(self, incidencia: Incidencia, resultado_yolo=None) -> ResultadoDifusion:
        destinatarios = self._destinatarios(incidencia)
        if not destinatarios:
            logger.warning("Incidencia #%s sin destinatarios de Telegram configurados.", incidencia.pk)
            return ResultadoDifusion()

        mensaje = self._formateador.alerta_incidencia(incidencia, resultado_yolo)
        alertas = []
        with ThreadPoolExecutor(max_workers=self._max_hilos) as pool:
            futuros = [pool.submit(self._enviar_a_uno, incidencia, destinatario, mensaje) for destinatario in destinatarios]
            for futuro in as_completed(futuros):
                try:
                    alertas.append(futuro.result())
                except Exception:
                    logger.exception("Fallo inesperado difundiendo la alerta de la incidencia #%s", incidencia.pk)
                finally:
                    close_old_connections()

        exitosas = [a for a in alertas if a.fue_exitosa and a.fecha_envio]
        t_primera = min((a.fecha_envio for a in exitosas), default=None)
        return ResultadoDifusion(alertas=alertas, t_primera_confirmacion=t_primera, exito=bool(exitosas))

    def _destinatarios(self, incidencia: Incidencia) -> list:
        vehiculo = incidencia.dispositivo.vehiculo
        accesos = list(
            AccesoTelegram.objects.filter(vehiculo=vehiculo, activo=True, recibe_alertas=True).exclude(chat_id=""),
        )
        ahora = timezone.now()
        accesos = [a for a in accesos if not (a.silenciado_hasta and a.silenciado_hasta > ahora)]

        resultado = [
            Destinatario(chat_id=a.chat_id, puede_controlar=a.puede_controlar, rol=a.rol, nombre=a.nombres)
            for a in accesos
        ]
        chats_incluidos = {d.chat_id for d in resultado}

        for chat_admin in config.telegram.admin_chat_ids:
            if chat_admin and chat_admin not in chats_incluidos:
                resultado.append(Destinatario(chat_id=chat_admin, puede_controlar=True, rol="ADMIN", nombre="Administrador"))
                chats_incluidos.add(chat_admin)

        if not resultado:
            conductor = vehiculo.conductor
            if conductor.telegram_chat_id:
                resultado.append(Destinatario(
                    chat_id=conductor.telegram_chat_id, puede_controlar=True,
                    rol="PROPIETARIO", nombre=conductor.nombre_completo,
                ))
        return resultado

    def _enviar_a_uno(self, incidencia: Incidencia, destinatario: Destinatario, mensaje: str) -> AlertaTelegram:
        conductor = incidencia.dispositivo.vehiculo.conductor
        imagen_campo = incidencia.imagen_anotada or incidencia.imagen
        with _LOCK_ESCRITURA_BD:
            alerta = AlertaTelegram.objects.create(
                incidencia=incidencia, conductor=conductor, chat_id=destinatario.chat_id,
                mensaje=mensaje, con_imagen=bool(imagen_campo), estado_envio="PENDIENTE",
            )

        if not config.telegram.habilitado:
            alerta.estado_envio = "ENVIADO"
            alerta.codigo_http = 0
            alerta.error = "simulado"
            alerta.fecha_envio = timezone.now()
            with _LOCK_ESCRITURA_BD:
                alerta.save(update_fields=["estado_envio", "codigo_http", "error", "fecha_envio"])
            return alerta

        teclado = self._teclados.alerta(incidencia, destinatario.puede_controlar)
        self._enviar_con_reintentos(alerta, imagen_campo, mensaje, teclado)
        return alerta

    def _enviar_con_reintentos(self, alerta: AlertaTelegram, imagen_campo, mensaje: str, teclado: dict) -> None:
        for intento in range(1, config.telegram.reintentos + 1):
            alerta.intentos = intento
            # El envío HTTP (lo lento) queda fuera del lock: solo se serializan
            # las escrituras a SQLite (rápidas), para no perder el paralelismo
            # entre destinatarios y evitar "database table is locked".
            if imagen_campo:
                with imagen_campo.open("rb") as archivo:
                    resp = self._cliente.send_photo(alerta.chat_id, archivo.read(), caption=mensaje, reply_markup=teclado)
            else:
                resp = self._cliente.send_message(alerta.chat_id, mensaje, reply_markup=teclado)

            alerta.codigo_http = resp.get("_status_code")
            if resp.get("ok"):
                alerta.estado_envio = "ENVIADO"
                alerta.message_id = (resp.get("result") or {}).get("message_id")
                alerta.fecha_envio = timezone.now()
                alerta.error = ""
                with _LOCK_ESCRITURA_BD:
                    alerta.save()
                return
            alerta.error = str(resp.get("description", resp))[:500]
            if intento < config.telegram.reintentos:
                time.sleep(ESPERAS_REINTENTO_S[min(intento - 1, len(ESPERAS_REINTENTO_S) - 1)])

        alerta.estado_envio = "FALLIDO"
        with _LOCK_ESCRITURA_BD:
            alerta.save()
