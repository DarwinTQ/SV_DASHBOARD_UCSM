"""Cliente MQTT del servidor (``paho-mqtt`` v2) y publicador de configuración.

Es lo único del proyecto que habla MQTT con el broker Mosquitto: el worker
``PuenteMQTT`` lo usa para recibir, y ``PublicadorConfiguracion`` para enviar
el tópico ``config`` retenido al nodo.

Contrato de tópicos (sección 2, **fijo**): ``ucsm/v1/<codigo>/<sufijo>``.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Callable, Optional

from config import config

logger = logging.getLogger("monitoreo.mqtt")

# Sufijos a los que se suscribe el servidor (sección 5.3).
SUFIJOS_SUSCRIPCION = (
    "telemetria", "gps", "evento", "estado", "modo", "sync/req", "cam/estado",
)


@dataclass
class MensajeMqtt:
    """Un mensaje recibido, ya con su marca de recepción del servidor."""

    topico: str
    codigo: str
    sufijo: str
    carga: dict
    t_recepcion_ms: int
    retenido: bool = False


class ClienteMqtt:
    """Envuelve ``paho.mqtt.client`` con reconexión automática y callbacks v2.

    La API v2 de paho (``CallbackAPIVersion.VERSION2``) es la única soportada
    en paho-mqtt 2.x; fijarla explícitamente evita el ``DeprecationWarning`` y
    el cambio de firma de los callbacks.
    """

    def __init__(self, client_id: Optional[str] = None, al_recibir: Optional[Callable] = None,
                 al_conectar: Optional[Callable] = None, suscripciones: Optional[tuple] = None):
        self._al_recibir = al_recibir
        self._al_conectar = al_conectar
        self._suscripciones = suscripciones if suscripciones is not None else SUFIJOS_SUSCRIPCION
        self._client_id = client_id or config.mqtt.client_id
        self._conectado = threading.Event()
        self._cliente = None
        self.reconexiones = 0
        self.recibidos = 0
        self.publicados = 0
        self.invalidos = 0

    # ------------------------------------------------------------------ #
    # Ciclo de vida
    # ------------------------------------------------------------------ #
    def conectar(self) -> bool:
        """Crea el cliente y arranca su hilo de red. No bloquea."""
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            logger.error(
                "paho-mqtt no está instalado: el puente MQTT queda desactivado. "
                "Instálalo con 'pip install -r requirements.txt'."
            )
            return False

        self._cliente = mqtt.Client(
            callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            clean_session=True,
            protocol=mqtt.MQTTv311,
        )
        if config.mqtt.usuario:
            self._cliente.username_pw_set(config.mqtt.usuario, config.mqtt.clave)
        self._cliente.on_connect = self._on_connect
        self._cliente.on_disconnect = self._on_disconnect
        self._cliente.on_message = self._on_message
        # Reconexión exponencial a cargo de paho (1 s → 30 s).
        self._cliente.reconnect_delay_set(min_delay=1, max_delay=30)

        try:
            self._cliente.connect_async(config.mqtt.host, config.mqtt.puerto, config.mqtt.keepalive)
            self._cliente.loop_start()
        except OSError as exc:
            logger.error("No se pudo conectar al broker MQTT %s:%s — %s",
                         config.mqtt.host, config.mqtt.puerto, exc)
            return False

        logger.info("Cliente MQTT '%s' iniciado hacia %s:%s (prefijo %s)",
                    self._client_id, config.mqtt.host, config.mqtt.puerto, config.mqtt.prefijo)
        return True

    def desconectar(self) -> None:
        if self._cliente is None:
            return
        try:
            self._cliente.loop_stop()
            self._cliente.disconnect()
        except Exception:
            logger.debug("Error al cerrar el cliente MQTT (ignorado).", exc_info=True)
        self._conectado.clear()

    def conectado(self) -> bool:
        return self._conectado.is_set()

    def esperar_conexion(self, timeout: float = 5.0) -> bool:
        return self._conectado.wait(timeout)

    # ------------------------------------------------------------------ #
    # Publicación
    # ------------------------------------------------------------------ #
    def publicar(self, codigo: str, sufijo: str, carga: dict,
                 qos: int = 1, retener: bool = False) -> bool:
        if self._cliente is None:
            return False
        topico = config.mqtt.topico(codigo, sufijo)
        try:
            resultado = self._cliente.publish(topico, json.dumps(carga, ensure_ascii=False),
                                              qos=qos, retain=retener)
            self.publicados += 1
            return resultado.rc == 0
        except Exception:
            logger.exception("Falló la publicación MQTT en %s", topico)
            return False

    # ------------------------------------------------------------------ #
    # Callbacks de paho (API v2)
    # ------------------------------------------------------------------ #
    def _on_connect(self, cliente, userdata, flags, reason_code, properties=None):
        if reason_code != 0:
            logger.error("El broker rechazó la conexión MQTT: %s "
                         "(revisa MQTT_USUARIO/MQTT_CLAVE y el archivo acl)", reason_code)
            return

        self._conectado.set()
        for sufijo in self._suscripciones:
            topico = config.mqtt.comodin(sufijo)
            cliente.subscribe(topico, qos=1)
            logger.debug("Suscrito a %s", topico)
        logger.info("Conectado al broker MQTT; %d suscripciones activas.", len(self._suscripciones))

        if self._al_conectar is not None:
            try:
                self._al_conectar()
            except Exception:
                logger.exception("Error en el callback de conexión MQTT (no crítico).")

    def _on_disconnect(self, cliente, userdata, flags=None, reason_code=None, properties=None):
        if self._conectado.is_set():
            self.reconexiones += 1
        self._conectado.clear()
        logger.warning("Desconectado del broker MQTT (%s); paho reintenta solo.", reason_code)

    def _on_message(self, cliente, userdata, mensaje):
        # LO PRIMERO: la marca de recepción del servidor (sección 5.3). Es el
        # t_recepcion_servidor de medicion_tiempo, así que no puede esperar a
        # que se parsee el JSON.
        import time

        t_recepcion_ms = time.time_ns() // 1_000_000
        self.recibidos += 1

        try:
            carga = json.loads(mensaje.payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.invalidos += 1
            logger.warning("Payload MQTT no es JSON válido en %s", mensaje.topic)
            return
        if not isinstance(carga, dict):
            self.invalidos += 1
            logger.warning("Payload MQTT no es un objeto JSON en %s", mensaje.topic)
            return

        msg = MensajeMqtt(
            topico=mensaje.topic,
            codigo=config.mqtt.codigo_de_topico(mensaje.topic),
            sufijo=config.mqtt.sufijo_de_topico(mensaje.topic),
            carga=carga,
            t_recepcion_ms=t_recepcion_ms,
            retenido=bool(mensaje.retain),
        )
        if self._al_recibir is None:
            return
        try:
            self._al_recibir(msg)
        except Exception:
            logger.exception("Error procesando el mensaje MQTT de %s", mensaje.topic)


class PublicadorConfiguracion:
    """Publica ``ucsm/v1/<codigo>/config`` **retenido** (sección 5.3).

    Al ser retenido, el nodo recibe el armado y los umbrales vigentes en cuanto
    se conecta, sin que el servidor tenga que recordar reenviárselos. Se llama
    al arrancar y cada vez que cambia ``armado`` (web, bot ``/armar``) o los
    umbrales (página Configuración).
    """

    _instancia: Optional["PublicadorConfiguracion"] = None
    _instancia_lock = threading.Lock()

    def __init__(self, cliente: Optional[ClienteMqtt] = None):
        self._cliente = cliente

    @classmethod
    def instancia(cls) -> "PublicadorConfiguracion":
        with cls._instancia_lock:
            if cls._instancia is None:
                cls._instancia = cls()
            return cls._instancia

    def registrar_cliente(self, cliente: ClienteMqtt) -> None:
        """Lo llama ``PuenteMQTT`` al arrancar, para compartir su conexión."""
        self._cliente = cliente

    @staticmethod
    def carga_para(dispositivo) -> dict:
        return {"armado": dispositivo.armado, "umbrales": config.deteccion.a_dict()}

    def publicar(self, dispositivo) -> bool:
        """Publica la configuración de un dispositivo. Silencioso si no hay broker."""
        if self._cliente is None or not self._cliente.conectado():
            logger.debug("Sin conexión MQTT: no se publicó config de %s", dispositivo.codigo)
            return False
        enviado = self._cliente.publicar(
            dispositivo.codigo, "config", self.carga_para(dispositivo), qos=1, retener=True,
        )
        if enviado:
            logger.info("config publicada (retenida) para %s: armado=%s",
                        dispositivo.codigo, dispositivo.armado)
        return enviado

    def publicar_todos(self) -> int:
        from monitoreo.models import Dispositivo

        enviados = 0
        for dispositivo in Dispositivo.objects.all():
            if self.publicar(dispositivo):
                enviados += 1
        return enviados

    def enviar_comando(self, dispositivo, accion: str, **extra) -> bool:
        """``cmd``: reiniciar, recalibrar_piso, forzar_modo (QoS 1, sin retener)."""
        if self._cliente is None or not self._cliente.conectado():
            return False
        carga = {"accion": accion, **extra}
        return self._cliente.publicar(dispositivo.codigo, "cmd", carga, qos=1, retener=False)

    def enviar_comando_camara(self, codigo: str, **opciones) -> bool:
        """``cam/cmd``: resolucion, calidad, fps_max, flash, reiniciar (sección 5.5)."""
        if self._cliente is None or not self._cliente.conectado():
            return False
        return self._cliente.publicar(codigo, "cam/cmd", opciones, qos=1, retener=False)
