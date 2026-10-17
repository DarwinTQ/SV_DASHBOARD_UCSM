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

Cambio del modelo propio de la tesis: la confirmación ya no es solo de
``person``. Se lleva una **racha y un cooldown independientes por clase**
(``arma_fuego``, ``arma_blanca``, ``abriendo_puerta``, ``car broken``,
``suspicious``, ``person``), cada una con su umbral de confianza y su
``TipoEvento``, según ``config.yolo.clases_vigiladas``. La restricción de
"solo ESTACIONADO y armado" se aplica **por clase**: un arma es una emergencia
también con el vehículo en marcha, una persona no.
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

    def __init__(self, proveedor_cuadros=None, incidencia_service=None):
        super().__init__()
        self._estado_en_vivo = EstadoEnVivo.instancia()
        self._proveedor = proveedor_cuadros or ProveedorCuadros()
        # Inyectable para las pruebas (así no se construye un DifusorAlertas
        # real, que hablaría con la API de Telegram de verdad).
        self._incidencias = incidencia_service
        self._fps_historial: dict[str, deque] = defaultdict(lambda: deque(maxlen=20))
        # Racha y cooldown por (codigo de dispositivo, clase de YOLO).
        self._frames_confirmados: dict[tuple, list] = defaultdict(list)
        self._ultima_incidencia: dict[tuple, float] = {}
        self._ultimo_envio_navegador: dict[str, float] = {}
        self._aviso_modo_emitido: dict[tuple, float] = {}

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
        self._evaluar_confirmaciones(dispositivo, frame)

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
        detecciones = resultado.num_detecciones

        carpeta_live = config.bd.media_root / "live"
        carpeta_live.mkdir(parents=True, exist_ok=True)
        (carpeta_live / f"{codigo}.jpg").write_bytes(imagen)

        url = f"/media/live/{codigo}.jpg?ts={RelojServidor.ahora_ms()}"
        self._estado_en_vivo.actualizar_frame(codigo, url, fps or 0, detecciones, latencia_captura_ms or 0)

        # Tope de FPS hacia el navegador: el ciclo de visión puede ir más rápido
        # que lo que tiene sentido empujar por el WebSocket.
        ahora = time.monotonic()
        periodo = 1.0 / max(1, config.stream.fps_navegador)
        if ahora - self._ultimo_envio_navegador.get(codigo, 0.0) < periodo:
            return
        self._ultimo_envio_navegador[codigo] = ahora

        NotificadorTiempoReal.frame({
            "codigo": codigo, "url": url, "fps": fps,
            "personas": detecciones, "latencia_captura_ms": latencia_captura_ms,
            "clases": resultado.clases_detectadas, "resumen_clases": resultado.resumen_clases,
            "metodo": captura.metodo, "modo": self._estado_en_vivo.modo(codigo),
            "bytes": len(imagen),
        })

    # ------------------------------------------------------------------ #
    # Confirmación por clase
    # ------------------------------------------------------------------ #
    def _puede_alertar(self, dispositivo: Dispositivo, vigilada) -> bool:
        """¿Se puede alertar esta clase con el estado actual del vehículo?

        Las clases con ``solo_estacionado=False`` (armas, daño al vehículo)
        alertan siempre: son una emergencia en cualquier modo y no depender del
        armado evita quedarse callado justo cuando importa. ``person`` conserva
        la regla de la sección 5.4 (en marcha siempre hay gente dentro).
        """
        if not vigilada.solo_estacionado:
            return True

        if not dispositivo.armado:
            return False
        modo = self._estado_en_vivo.modo(dispositivo.codigo)
        if modo == "ESTACIONADO":
            return True

        # Sin modo todavía (el nodo no publicó 'modo') se es conservador y no se
        # alerta, pero se avisa en el log como máximo una vez por minuto.
        clave = (dispositivo.codigo, vigilada.clase)
        ahora = time.monotonic()
        if ahora - self._aviso_modo_emitido.get(clave, 0.0) > 60:
            self._aviso_modo_emitido[clave] = ahora
            self.logger.info(
                "%s no se alerta en %s: modo = %s (esta clase solo alerta ESTACIONADO y armado).",
                vigilada.tipo_evento, dispositivo.codigo, modo or "desconocido",
            )
        return False

    def _puede_alertar_persona(self, dispositivo: Dispositivo) -> bool:
        """Atajo histórico para la clase ``person`` (sección 5.4)."""
        vigilada = config.yolo.vigilada("person")
        return bool(vigilada) and self._puede_alertar(dispositivo, vigilada)

    def _evaluar_confirmaciones(self, dispositivo: Dispositivo, frame: FrameProcesado) -> None:
        """Una racha por clase: ``arma_fuego`` y ``suspicious`` se cuentan por
        separado, así una no "roba" los frames de confirmación de la otra."""
        codigo = dispositivo.codigo
        presentes: set[str] = set()

        for clase in frame.resultado.clases_detectadas:
            vigilada = config.yolo.vigilada(clase)
            if vigilada is None or not self._puede_alertar(dispositivo, vigilada):
                continue
            presentes.add(clase)
            self._frames_confirmados[(codigo, clase)].append(frame)

        # Lo que dejó de verse (o ya no puede alertar) pierde su racha: nada de
        # alertas acumuladas de hace diez segundos.
        for clave in [c for c in self._frames_confirmados if c[0] == codigo and c[1] not in presentes]:
            self._frames_confirmados.pop(clave, None)

        for clase in presentes:
            self._intentar_incidencia(dispositivo, clase)

    def _servicio_incidencias(self) -> IncidenciaService:
        """Se crea perezosamente: construirlo en ``__init__`` levantaría el
        cliente de Telegram antes de que ``main.py`` termine de arrancar."""
        if self._incidencias is None:
            self._incidencias = IncidenciaService()
        return self._incidencias

    def _intentar_incidencia(self, dispositivo: Dispositivo, clase: str) -> None:
        clave = (dispositivo.codigo, clase)
        confirmados = self._frames_confirmados[clave]
        if len(confirmados) < config.vision.frames_confirmacion:
            return

        if time.time() - self._ultima_incidencia.get(clave, 0) < config.vision.cooldown_s:
            confirmados.clear()
            return

        vigilada = config.yolo.vigilada(clase)
        primero = confirmados[0]
        self._servicio_incidencias().registrar_incidencia_visual(
            dispositivo=dispositivo, captura=primero.captura, resultado_yolo=primero.resultado,
            tipo_codigo=vigilada.tipo_evento, clase_yolo=clase,
            t_deteccion_sensor=primero.t_solicitud, t_recepcion_servidor=primero.t_frame,
            t_inicio_inferencia=primero.t_inicio_inferencia, t_fin_inferencia=primero.t_fin_inferencia,
        )
        self._ultima_incidencia[clave] = time.time()
        confirmados.clear()
