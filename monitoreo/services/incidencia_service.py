"""Pipeline completo de una incidencia: captura -> YOLO -> BD -> Telegram ->
métricas (sección 8.1). Lo ejecuta ``ProcesadorEventos`` en su hilo para los
eventos de sensor, y ``VigilanciaVisual`` para las incidencias PERSONA
detectadas por la vigilancia visual continua (sección 8.2).
"""
from __future__ import annotations

import logging
from typing import Optional

from django.core.files.base import ContentFile
from django.db import transaction

from config import config
from monitoreo.models import Dispositivo, DeteccionYolo, Incidencia, MedicionTiempo, TipoEvento
from monitoreo.services.camara_service import FabricaCamara, ResultadoCaptura
from monitoreo.services.detector_yolo import DetectorYOLO, ResultadoDeteccion
from monitoreo.services.difusor_alertas import DifusorAlertas
from monitoreo.services.mapas_service import MapasService
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal
from monitoreo.services.reloj_service import RelojServidor

logger = logging.getLogger("monitoreo.servicios")


class IncidenciaService:
    """Orquesta el pipeline completo. Las dependencias se pueden inyectar
    (pruebas con dobles de cámara/YOLO/difusor de alertas)."""

    def __init__(self, difusor_alertas=None, mapas_service=None, detector_yolo=None, fabrica_camara=None):
        self._difusor = difusor_alertas or DifusorAlertas()
        self._mapas = mapas_service or MapasService()
        self._detector = detector_yolo
        self._fabrica_camara = fabrica_camara or FabricaCamara

    def _obtener_detector(self):
        return self._detector or DetectorYOLO.instancia()

    def procesar_evento_sensor(self, evento: dict) -> Optional[Incidencia]:
        codigo = evento["codigo"]
        dispositivo = (
            Dispositivo.objects.filter(codigo=codigo)
            .select_related("vehiculo", "vehiculo__conductor").first()
        )
        if dispositivo is None:
            logger.error("IncidenciaService: dispositivo '%s' ya no existe; se descarta el evento.", codigo)
            return None

        ts_ms = evento.get("ts_ms")
        sin_reloj_sensor = ts_ms is None
        t_recepcion_servidor = RelojServidor.ms_a_datetime(evento["t_recepcion_ms"])
        t_deteccion_sensor = RelojServidor.ms_a_datetime(ts_ms) if ts_ms else t_recepcion_servidor

        captura = self._fabrica_camara.para(codigo).capturar()

        resultado_yolo = ResultadoDeteccion()
        t_inicio_inferencia = t_fin_inferencia = None
        if captura.exito:
            t_inicio_inferencia = RelojServidor.ahora()
            resultado_yolo = self._obtener_detector().detectar(captura.jpg)
            t_fin_inferencia = RelojServidor.ahora()

        tipo_codigo_original = evento["tipo"]
        observaciones = []
        if sin_reloj_sensor:
            observaciones.append("Sin reloj del sensor (ts_ms nulo): se usó la hora de recepción del servidor.")
        if not captura.exito:
            observaciones.append(f"No se pudo capturar imagen de la cámara: {captura.error}.")

        tipo_final_codigo = tipo_codigo_original
        if resultado_yolo.hay_persona:
            tipo_final_codigo = "PERSONA"
            unidad = evento.get("unidad", "")
            observaciones.append(
                f"Disparado por sensor: {tipo_codigo_original} "
                f"({evento.get('valor')} {unidad} ≥ {evento.get('umbral')} {unidad})."
            )

        gps = evento.get("gps") or {}
        latitud = gps.get("lat") if gps.get("fix") else None
        longitud = gps.get("lon") if gps.get("fix") else None

        return self._persistir_y_notificar(
            dispositivo=dispositivo, tipo_codigo=tipo_final_codigo, fecha_hora=t_deteccion_sensor,
            aceleracion_g=evento.get("aceleracion_g"), nivel_audio_db=evento.get("nivel_audio_db"),
            latitud=latitud, longitud=longitud, captura=captura, resultado_yolo=resultado_yolo,
            observacion=" ".join(observaciones),
            t_deteccion_sensor=t_deteccion_sensor, t_recepcion_servidor=t_recepcion_servidor,
            t_inicio_inferencia=t_inicio_inferencia, t_fin_inferencia=t_fin_inferencia,
            sin_reloj_sensor=sin_reloj_sensor,
        )

    def registrar_incidencia_persona(
        self, dispositivo: Dispositivo, captura: ResultadoCaptura, resultado_yolo: ResultadoDeteccion,
        t_deteccion_sensor, t_recepcion_servidor, t_inicio_inferencia, t_fin_inferencia,
    ) -> Incidencia:
        """Usado por VigilanciaVisual: la captura y la inferencia ya ocurrieron
        en el ciclo continuo de YOLO, aquí solo se persiste y se notifica."""
        return self._persistir_y_notificar(
            dispositivo=dispositivo, tipo_codigo="PERSONA", fecha_hora=t_deteccion_sensor,
            aceleracion_g=None, nivel_audio_db=None, latitud=None, longitud=None,
            captura=captura, resultado_yolo=resultado_yolo,
            observacion="Detectado por vigilancia visual continua (YOLO).",
            t_deteccion_sensor=t_deteccion_sensor, t_recepcion_servidor=t_recepcion_servidor,
            t_inicio_inferencia=t_inicio_inferencia, t_fin_inferencia=t_fin_inferencia,
            sin_reloj_sensor=False,
        )

    def _persistir_y_notificar(
        self, *, dispositivo, tipo_codigo, fecha_hora, aceleracion_g, nivel_audio_db,
        latitud, longitud, captura, resultado_yolo, observacion,
        t_deteccion_sensor, t_recepcion_servidor, t_inicio_inferencia, t_fin_inferencia, sin_reloj_sensor,
    ) -> Incidencia:
        tipo = TipoEvento.objects.get(codigo=tipo_codigo)
        nombre_archivo = self._nombre_archivo(fecha_hora, dispositivo.codigo)

        with transaction.atomic():
            incidencia = Incidencia(
                dispositivo=dispositivo, tipo=tipo, fecha_hora=fecha_hora,
                aceleracion_g=aceleracion_g, nivel_audio_db=nivel_audio_db,
                latitud=latitud, longitud=longitud, observacion=observacion.strip(),
            )
            if captura.exito and captura.jpg:
                incidencia.imagen.save(nombre_archivo, ContentFile(captura.jpg), save=False)
            if resultado_yolo.imagen_anotada_jpg:
                incidencia.imagen_anotada.save(
                    f"anot_{nombre_archivo}", ContentFile(resultado_yolo.imagen_anotada_jpg), save=False,
                )
            incidencia.save()

            DeteccionYolo.objects.bulk_create([
                DeteccionYolo(
                    incidencia=incidencia, clase=caja.clase, confianza=caja.confianza,
                    x1=caja.x1, y1=caja.y1, x2=caja.x2, y2=caja.y2, modelo_yolo=config.yolo.nombre_modelo,
                )
                for caja in resultado_yolo.cajas
            ])

        t_registro_bd = RelojServidor.ahora()
        self._notificar_nueva(incidencia, resultado_yolo)

        t_envio_telegram = t_confirmacion_telegram = None
        alerta_estado = None
        if config.telegram.enviar_sin_persona or resultado_yolo.hay_persona:
            t_envio_telegram = RelojServidor.ahora()
            resultado_difusion = self._difusor.difundir(incidencia, resultado_yolo)
            t_confirmacion_telegram = resultado_difusion.t_primera_confirmacion or RelojServidor.ahora()
            alerta_estado = "ENVIADO" if resultado_difusion.exito else "FALLIDO"
            if resultado_difusion.exito:
                incidencia.estado = "NOTIFICADA"
                incidencia.save(update_fields=["estado"])

        medicion = MedicionTiempo(
            incidencia=incidencia, t_deteccion_sensor=t_deteccion_sensor, t_recepcion_servidor=t_recepcion_servidor,
            t_inicio_inferencia=t_inicio_inferencia, t_fin_inferencia=t_fin_inferencia,
            t_registro_bd=t_registro_bd, t_envio_telegram=t_envio_telegram, t_confirmacion_telegram=t_confirmacion_telegram,
        )
        medicion._sin_reloj_sensor = sin_reloj_sensor
        medicion.save()

        NotificadorTiempoReal.incidencia_actualizada({
            "id": incidencia.pk, "estado": incidencia.estado, "alerta": alerta_estado,
            "tiempo_total_ms": medicion.tiempo_respuesta_total_ms,
        })

        if config.maps.geocodificar and incidencia.tiene_coordenadas:
            self._agregar_direccion_aproximada(incidencia)

        return incidencia

    def _agregar_direccion_aproximada(self, incidencia: Incidencia) -> None:
        try:
            direccion = self._mapas.direccion_aproximada(float(incidencia.latitud), float(incidencia.longitud))
            if direccion:
                incidencia.observacion = f"{incidencia.observacion} Dirección aprox.: {direccion}.".strip()
                incidencia.save(update_fields=["observacion"])
        except Exception:
            logger.exception("Falló la geocodificación inversa (no crítico).")

    @staticmethod
    def _nombre_archivo(fecha_hora, codigo: str) -> str:
        return f"evt_{fecha_hora:%Y%m%d_%H%M%S}_{fecha_hora.microsecond // 1000:03d}_{codigo}.jpg"

    @staticmethod
    def _notificar_nueva(incidencia: Incidencia, resultado_yolo: ResultadoDeteccion) -> None:
        NotificadorTiempoReal.incidencia_nueva({
            "id": incidencia.pk, "tipo": incidencia.tipo.codigo, "nombre_tipo": incidencia.tipo.nombre,
            "riesgo": incidencia.tipo.nivel_riesgo, "placa": incidencia.dispositivo.vehiculo.placa,
            "fecha_hora": incidencia.fecha_hora.isoformat(),
            "miniatura": (
                incidencia.imagen_anotada.url if incidencia.imagen_anotada
                else (incidencia.imagen.url if incidencia.imagen else None)
            ),
            "lat": float(incidencia.latitud) if incidencia.latitud is not None else None,
            "lon": float(incidencia.longitud) if incidencia.longitud is not None else None,
            "personas": resultado_yolo.num_personas, "confianza_max": resultado_yolo.confianza_max,
        })
