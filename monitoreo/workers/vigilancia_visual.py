"""YOLO continuo sobre la cámara en vivo (sección 8.2)."""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from config import config
from monitoreo.models import Dispositivo
from monitoreo.services.camara_service import FabricaCamara, ResultadoCaptura
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

    def __init__(self):
        super().__init__()
        self._estado_en_vivo = EstadoEnVivo.instancia()
        self._fps_historial: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        self._frames_confirmados: dict[str, list] = defaultdict(list)
        self._ultima_incidencia_persona: dict[str, float] = {}

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
        captura = FabricaCamara.para(codigo).capturar()
        t_frame = RelojServidor.ahora()
        if not captura.exito:
            return

        t_inicio_inferencia = RelojServidor.ahora()
        resultado = DetectorYOLO.instancia().detectar(captura.jpg)
        t_fin_inferencia = RelojServidor.ahora()

        fps = self._actualizar_fps(codigo, captura.captura_ms)
        self._guardar_frame_en_vivo(codigo, captura, resultado, fps, captura.captura_ms)

        frame = FrameProcesado(t_solicitud, t_frame, t_inicio_inferencia, t_fin_inferencia, captura, resultado)
        self._evaluar_confirmacion_persona(dispositivo, frame)

    def _actualizar_fps(self, codigo: str, captura_ms) -> float | None:
        historial = self._fps_historial[codigo]
        if captura_ms:
            historial.append(1000 / captura_ms)
        return round(sum(historial) / len(historial), 2) if historial else None

    def _guardar_frame_en_vivo(self, codigo: str, captura: ResultadoCaptura, resultado: ResultadoDeteccion, fps, latencia_captura_ms) -> None:
        imagen = resultado.imagen_anotada_jpg or captura.jpg
        if imagen is None:
            return

        carpeta_live = config.bd.media_root / "live"
        carpeta_live.mkdir(parents=True, exist_ok=True)
        (carpeta_live / f"{codigo}.jpg").write_bytes(imagen)

        url = f"/media/live/{codigo}.jpg?ts={RelojServidor.ahora_ms()}"
        self._estado_en_vivo.actualizar_frame(codigo, url, fps or 0, resultado.num_personas, latencia_captura_ms or 0)
        NotificadorTiempoReal.frame({
            "codigo": codigo, "url": url, "fps": fps,
            "personas": resultado.num_personas, "latencia_captura_ms": latencia_captura_ms,
        })

    def _evaluar_confirmacion_persona(self, dispositivo: Dispositivo, frame: FrameProcesado) -> None:
        codigo = dispositivo.codigo
        confirmados = self._frames_confirmados[codigo]

        if not frame.resultado.hay_persona:
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
