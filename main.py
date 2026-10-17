"""Servidor principal del Sistema de Monitoreo Vehicular Inteligente (UCSM).

Punto de arranque único: ``python main.py``. Nunca uses ``manage.py runserver``
para producción: este script configura logging, prepara el entorno, aplica
migraciones, activa SQLite en modo WAL, calienta YOLO, arranca los workers de
fondo y finalmente levanta Daphne (ASGI) para servir HTTP y WebSocket juntos.
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import os
import socket
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

logger = logging.getLogger("main")


class ServidorPrincipal:
    """Orquesta el arranque y apagado ordenados de todo el sistema."""

    def __init__(self, host: str | None = None, puerto: int | None = None,
                 sin_vision: bool = False, simular: bool = False, solo_migrar: bool = False,
                 sin_mqtt: bool = False):
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", "sv_ucsm.settings")
        import django

        django.setup()

        from config import config  # después de django.setup() para registrar logging primero

        self.config = config
        self.host = host or config.servidor.host
        self.puerto = puerto or config.servidor.puerto
        self.simular = simular
        self.solo_migrar = solo_migrar
        self.sin_mqtt = sin_mqtt

        if sin_vision:
            self.config.vision = dataclasses.replace(self.config.vision, activa=False)
        if simular:
            self.config.esp32cam = dataclasses.replace(self.config.esp32cam, simulada=True)
            self.config.telegram = dataclasses.replace(self.config.telegram, habilitado=False)

        self._workers = []
        self._bot_telegram = None
        self._puente_mqtt = None

    # ------------------------------------------------------------------ #
    # Arranque
    # ------------------------------------------------------------------ #
    def ejecutar(self) -> None:
        self._verificar_entorno()
        self._migrar_y_sembrar()
        if self.solo_migrar:
            logger.info("Modo --solo-migrar: migraciones y catálogo listos, se finaliza sin levantar el servidor.")
            return

        logger.info("SQLite en modo WAL activado (múltiples hilos escriben a la vez).")
        self._calentar_yolo()
        self._arrancar_workers()
        self._imprimir_banner()
        try:
            self._levantar_daphne()
        finally:
            self._apagar()

    def _verificar_entorno(self) -> None:
        for carpeta in (self.config.bd.media_root, BASE_DIR / "logs", self.config.yolo.ruta_modelo.parent):
            carpeta.mkdir(parents=True, exist_ok=True)
        logger.info("Entorno verificado: media=%s, logs=%s, modelos_ia=%s",
                     self.config.bd.media_root, BASE_DIR / "logs", self.config.yolo.ruta_modelo.parent)

    def _migrar_y_sembrar(self) -> None:
        from django.core.management import call_command

        logger.info("Aplicando migraciones…")
        call_command("migrate", interactive=False, verbosity=0)
        logger.info("Sembrando catálogo de tipos de evento…")
        call_command("sembrar_catalogos", demo=self.simular, verbosity=0)

    def _calentar_yolo(self) -> None:
        from monitoreo.services.detector_yolo import DetectorYOLO

        detector = DetectorYOLO.instancia()
        if detector.disponible:
            detector.calentar()
        else:
            logger.warning("YOLO no disponible: el sistema seguirá funcionando sin detección de personas.")

    def _arrancar_workers(self) -> None:
        from monitoreo.workers.bot_telegram import BotTelegramWorker
        from monitoreo.workers.monitor_dispositivos import MonitorDispositivos
        from monitoreo.workers.procesador_eventos import ProcesadorEventos
        from monitoreo.workers.puente_mqtt import PuenteMQTT
        from monitoreo.workers.receptor_stream import ReceptorStreamCamara
        from monitoreo.workers.vigilancia_visual import VigilanciaVisual

        # El puente MQTT va PRIMERO: así ya está suscrito (y publica la config
        # retenida) cuando el nodo intente conectarse.
        self._puente_mqtt = PuenteMQTT()
        self._workers = [self._puente_mqtt, ProcesadorEventos.instancia(), MonitorDispositivos()]

        if self.sin_mqtt:
            logger.info("Puente MQTT desactivado (--sin-mqtt): solo queda el respaldo HTTP.")
            self._workers.remove(self._puente_mqtt)
            self._puente_mqtt = None

        # Lee el stream MJPEG de la cámara para que YOLO y el pipeline tomen el
        # cuadro de memoria en vez de pedir una foto nueva (sección 5.5).
        self._workers.append(ReceptorStreamCamara())

        if self.config.vision.activa:
            self._workers.append(VigilanciaVisual())
        else:
            logger.info("Vigilancia visual continua desactivada (--sin-vision o VISION_CONTINUA=False).")

        self._bot_telegram = BotTelegramWorker()
        self._workers.append(self._bot_telegram)

        for worker in self._workers:
            worker.iniciar()

    def _ip_lan(self) -> str:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        except OSError:
            return "127.0.0.1"
        finally:
            s.close()

    def _estado_bot_telegram(self) -> str:
        if self._bot_telegram is None or not self._bot_telegram.disponible():
            return "sin token o desactivado (TELEGRAM_BOT_COMANDOS=False)"
        if self._bot_telegram.usuario_bot:
            return f"Bot @{self._bot_telegram.usuario_bot} activo (long polling)"
        return "no se pudo conectar (revisa TELEGRAM_BOT_TOKEN)"

    def _estado_mqtt(self) -> str:
        if self._puente_mqtt is None:
            return "desactivado (--sin-mqtt); solo respaldo HTTP"
        destino = f"{self.config.mqtt.host}:{self.config.mqtt.puerto}"
        if self._puente_mqtt.cliente.esperar_conexion(timeout=3.0):
            return f"conectado a {destino} · tópicos {self.config.mqtt.prefijo}/<codigo>/…"
        return (
            f"SIN conexión a {destino} — ¿arrancaste "
            "infra\\mosquitto\\iniciar_mosquitto.bat? (reintenta solo)"
        )

    def _imprimir_banner(self) -> None:
        ip_lan = self._ip_lan()
        urls_camara = ", ".join(
            f"{codigo}={url}" for codigo, url in self.config.esp32cam.urls_por_dispositivo.items()
        ) or "(ninguna configurada)"
        urls_stream = ", ".join(
            f"{codigo}={url}" for codigo, url in self.config.esp32cam.urls_stream_por_dispositivo.items()
        ) or "(ninguno; se usará /capture)"

        banner = f"""
========================================================================
 Sistema de Monitoreo Vehicular Inteligente (YOLO + IoT) · UCSM
========================================================================
 URL local:         http://127.0.0.1:{self.puerto}
 URL en la LAN:     http://{ip_lan}:{self.puerto}   (panel y API de respaldo)
 Broker MQTT:       {self._estado_mqtt()}
 Cámara(s):         {urls_camara}
 Stream MJPEG:      {urls_stream}
 Cámara simulada:   {self.config.esp32cam.simulada}
 Telegram:          {"habilitado" if self.config.telegram.habilitado else "deshabilitado (simulación)"}
 {self._estado_bot_telegram()}
 Google Maps:       {"configurado" if self.config.maps.api_key else "no configurado (se usa Leaflet/OSM)"}
 Vigilancia visual: {"activa" if self.config.vision.activa else "desactivada"}
========================================================================
"""
        logger.info(banner)
        print(banner)

    def _levantar_daphne(self) -> None:
        from daphne.endpoints import build_endpoint_description_strings
        from daphne.server import Server

        from sv_ucsm.asgi import application

        endpoints = build_endpoint_description_strings(host=self.host, port=self.puerto)
        logger.info("Levantando Daphne en %s…", endpoints)
        Server(application=application, endpoints=endpoints, signal_handlers=True).run()

    # ------------------------------------------------------------------ #
    # Apagado
    # ------------------------------------------------------------------ #
    def _apagar(self) -> None:
        logger.info("Apagando el servidor: deteniendo workers…")
        for worker in self._workers:
            worker.detener()
        logger.info("Apagado completo.")


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Servidor principal del sistema de monitoreo UCSM.")
    parser.add_argument("--host", default=None, help="Host de escucha (por defecto, config.servidor.host).")
    parser.add_argument("--puerto", type=int, default=None, help="Puerto de escucha (por defecto, config.servidor.puerto).")
    parser.add_argument("--sin-vision", action="store_true", help="Desactiva la vigilancia visual continua (YOLO).")
    parser.add_argument(
        "--simular", action="store_true",
        help="Cámara simulada + Telegram deshabilitado, para probar sin hardware (sección 11).",
    )
    parser.add_argument("--solo-migrar", action="store_true", help="Solo migra y siembra el catálogo; no levanta el servidor.")
    parser.add_argument(
        "--sin-mqtt", action="store_true",
        help="No arranca el puente MQTT (deja solo la API HTTP de respaldo). Útil si el broker no está instalado.",
    )
    return parser


def main() -> None:
    args = _construir_parser().parse_args()
    servidor = ServidorPrincipal(
        host=args.host, puerto=args.puerto, sin_vision=args.sin_vision,
        simular=args.simular, solo_migrar=args.solo_migrar, sin_mqtt=args.sin_mqtt,
    )
    try:
        servidor.ejecutar()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
