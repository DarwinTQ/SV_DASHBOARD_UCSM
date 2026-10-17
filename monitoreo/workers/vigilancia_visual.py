"""YOLO continuo sobre la cámara en vivo (sección 8.2).

Cambios de la v3.0.0 (sección 5.4 y 5.5):

* el cuadro sale del **buffer del stream MJPEG** que llena
  ``ReceptorStreamCamara``, en vez de pedir una foto a ``/capture`` en cada
  ciclo (eso costaba cientos de ms y limitaba los FPS);
* las incidencias **PERSONA solo se generan con el vehículo ESTACIONADO y
  armado**: en marcha siempre hay personas dentro del auto y toda alerta sería
  un falso positivo. El vídeo se sigue mostrando y anotando igual, sin alertar.
* los cuadros anotados se envían al navegador por WebSocket (binario, a lo sumo
  ``STREAM_FPS_NAVEGADOR``), solo si hay alguien mirando el panel.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from config import config
from monitoreo.models import Dispositivo
from monitoreo.services.camara_service import ProveedorCuadros, ResultadoCaptura
from monitoreo.services.detector_yolo import DetectorYOLO, ResultadoDeteccion
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.services.incidencia_service import IncidenciaService
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal
from monitoreo.services.reloj_service import RelojServidor

from .base_worker import WorkerBase


@dataclass
class FrameProcesado:
    t_solicitud: object
    t_frame: object
    t_inicio_inferencia: object
    t_fin_inferencia: object
    captura: ResultadoCaptura
    resultado: ResultadoDeteccion


class VigilanciaVisual(WorkerBase):
    """Un único hilo recorre todos los dispositivos ACTIVOS cada ``VISION_INTERVALO_MS``."""

    nombre = "VigilanciaVisual"

    def __init__(self, proveedor_cuadros=None):
        super().__init__()
        self._estado_en_vivo = EstadoEnVivo.instancia()
        self._proveedor = proveedor_cuadros or ProveedorCuadros()
        self._fps_historial: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        self._frames_confirmados: dict[str, list] = defaultdict(list)
        self._ultima_incidencia_persona: dict[str, float] = {}
        self._ultimo_envio_navegador: dict[str, float] = {}
        self._aviso_modo_emitido: dict[str, float] = {}

    def ciclo(self) -> None:
        if not config.vision.activa:
            time.sleep(1)
            return

        dispositivos = list(Dispositivo.objects.filter(estado="ACTIVO"))
        if not dispositivos:
            time.sleep(1)
            return

        inicio_ciclo = time.monotonic()
        for dispositivo in dispositivos:
            try:
                self._procesar_dispositivo(dispositivo)
            except Exception:
                self.logger.exception("Error vigilando el dispositivo %s", dispositivo.codigo)

        transcurrido = time.monotonic() - inicio_ciclo
        espera = max(0.0, (config.vision.intervalo_ms / 1000) - transcurrido)
        time.sleep(espera)

    def _procesar_dispositivo(self, dispositivo: Dispositivo) -> None:
        codigo = dispositivo.codigo
        t_solicitud = RelojServidor.ahora()
        captura = self._proveedor.mas_reciente(codigo)
        t_frame = RelojServidor.ahora()
        if not captura.exito:
            return

        t_inicio_inferencia = RelojServidor.ahora()
        resultado = DetectorYOLO.instancia().detectar(captura.jpg)
        t_fin_inferencia = RelojServidor.ahora()

        latencia_ms = (t_fin_inferencia - t_solicitud).total_seconds() * 1000
        fps = self._actualizar_fps(codigo, latencia_ms)
        self._guardar_frame_en_vivo(codigo, captura, resultado, fps, captura.captura_ms)

        frame = FrameProcesado(t_solicitud, t_frame, t_inicio_inferencia, t_fin_inferencia, captura, resultado)
        self._evaluar_confirmacion_persona(dispositivo, frame)

    def _actualizar_fps(self, codigo: str, latencia_ms) -> float | None:
        """FPS reales del ciclo de visión (captura + inferencia), no de la cámara."""
        historial = self._fps_historial[codigo]
        if latencia_ms and latencia_ms > 0:
            historial.append(1000 / latencia_ms)
        return round(sum(historial) / len(historial), 2) if historial else None

    def _guardar_frame_en_vivo(self, codigo: str, captura: ResultadoCaptura, resultado: ResultadoDeteccion,
                               fps, latencia_captura_ms) -> None:
        imagen = resultado.imagen_anotada_jpg or captura.jpg
        if imagen is None:
            return

        carpeta_live = config.bd.media_root / "live"
        carpeta_live.mkdir(parents=True, exist_ok=True)
        (carpeta_live / f"{codigo}.jpg").write_bytes(imagen)

        url = f"/media/live/{codigo}.jpg?ts={RelojServidor.ahora_ms()}"
        self._estado_en_vivo.actualizar_frame(codigo, url, fps or 0, resultado.num_personas, latencia_captura_ms or 0)

        # Tope de FPS hacia el navegador: el ciclo de visión puede ir más rápido
        # que lo que tiene sentido empujar por el WebSocket.
        ahora = time.monotonic()
        periodo = 1.0 / max(1, config.stream.fps_navegador)
        if ahora - self._ultimo_envio_navegador.get(codigo, 0.0) < periodo:
            return
        self._ultimo_envio_navegador[codigo] = ahora

        NotificadorTiempoReal.frame({
            "codigo": codigo, "url": url, "fps": fps,
            "personas": resultado.num_personas, "latencia_captura_ms": latencia_captura_ms,
            "metodo": captura.metodo, "modo": self._estado_en_vivo.modo(codigo),
            "bytes": len(imagen),
        })

    # ------------------------------------------------------------------ #
    # PERSONA: solo estacionado y armado
    # ------------------------------------------------------------------ #
    def _puede_alertar_persona(self, dispositivo: Dispositivo) -> bool:
        """En marcha siempre hay personas dentro del auto (sección 5.4)."""
        if not dispositivo.armado:
            return False
        modo = self._estado_en_vivo.modo(dispositivo.codigo)
        if modo == "ESTACIONADO":
            return True

        # Sin modo todavía (el nodo no publicó 'modo') se es conservador y no se
        # alerta, pero se avisa en el log como máximo una vez por minuto.
        ahora = time.monotonic()
        if ahora - self._aviso_modo_emitido.get(dispositivo.codigo, 0.0) > 60:
            self._aviso_modo_emitido[dispositivo.codigo] = ahora
            self.logger.info(
                "PERSONA no se alerta en %s: modo = %s (solo se alerta ESTACIONADO y armado).",
                dispositivo.codigo, modo or "desconocido",
            )
        return False

    def _evaluar_confirmacion_persona(self, dispositivo: Dispositivo, frame: FrameProcesado) -> None:
        codigo = dispositivo.codigo
        confirmados = self._frames_confirmados[codigo]

        if not frame.resultado.hay_persona:
            confirmados.clear()
            return

        if not self._puede_alertar_persona(dispositivo):
            confirmados.clear()
            return

        confirmados.append(frame)
        if len(confirmados) < config.vision.frames_confirmacion:
            return

        ultima = self._ultima_incidencia_persona.get(codigo, 0)
        if time.time() - ultima < config.vision.cooldown_s:
            confirmados.clear()
            return

        primero = confirmados[0]
        IncidenciaService().registrar_incidencia_persona(
            dispositivo=dispositivo, captura=primero.captura, resultado_yolo=primero.resultado,
            t_deteccion_sensor=primero.t_solicitud, t_recepcion_servidor=primero.t_frame,
            t_inicio_inferencia=primero.t_inicio_inferencia, t_fin_inferencia=primero.t_fin_inferencia,
        )
        self._ultima_incidencia_persona[codigo] = time.time()
        confirmados.clear()
