"""Pipeline completo de una incidencia: captura -> YOLO -> BD -> Telegram ->
métricas (sección 8.1). Lo ejecuta ``ProcesadorEventos`` en su hilo para los
eventos de sensor, y ``VigilanciaVisual`` para las incidencias PERSONA
detectadas por la vigilancia visual continua (sección 8.2).

Cambios de la v3.0.0 (sección 5.4):

* La imagen sale del **buffer del stream MJPEG**, eligiendo el cuadro más
  cercano a ``t_deteccion_sensor``; solo si no hay stream se pide ``/capture``.
  El método usado queda anotado en ``observacion``.
* La acción (alerta normal / alerta silenciosa / solo registro) la decide
  ``ReglasAccion`` a partir de ``tipo_evento.nivel_riesgo``, no el firmware.
  El ``nivel`` que propone el nodo se valida y se registra en ``observacion``.
* La ``evidencia`` numérica de la regla (energías por banda, piso de ruido,
  planitud, duración del estallido, pico de g, desfase sonido-golpe) se guarda
  como texto legible en ``observacion`` y resumida en el texto de Telegram.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.core.files.base import ContentFile
from django.db import transaction

from config import config
from monitoreo.models import Dispositivo, DeteccionYolo, Incidencia, MedicionTiempo, TipoEvento
from monitoreo.services.camara_service import ProveedorCuadros, ResultadoCaptura
from monitoreo.services.detector_yolo import DetectorYOLO, ResultadoDeteccion
from monitoreo.services.difusor_alertas import DifusorAlertas
from monitoreo.services.mapas_service import MapasService
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal
from monitoreo.services.reglas_accion import DescriptorEvidencia, ReglasAccion
from monitoreo.services.reloj_service import RelojServidor

logger = logging.getLogger("monitoreo.servicios")


class IncidenciaService:
    """Orquesta el pipeline completo. Las dependencias se pueden inyectar
    (pruebas con dobles de cámara/YOLO/difusor de alertas)."""

    def __init__(self, difusor_alertas=None, mapas_service=None, detector_yolo=None,
                 fabrica_camara=None, proveedor_cuadros=None):
        self._difusor = difusor_alertas or DifusorAlertas()
        self._mapas = mapas_service or MapasService()
        self._detector = detector_yolo
        self._proveedor = proveedor_cuadros or ProveedorCuadros(fabrica_camara=fabrica_camara)

    def _obtener_detector(self):
        return self._detector or DetectorYOLO.instancia()

    # ------------------------------------------------------------------ #
    # Entradas del pipeline
    # ------------------------------------------------------------------ #
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

        # El cuadro más cercano al instante del sensor: la imagen de cuando
        # realmente pasó el golpe, no la de varios cientos de ms después.
        captura = self._proveedor.cercano_a(codigo, ts_ms or evento["t_recepcion_ms"])

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
        observaciones.append(self._nota_imagen(captura))
        observaciones.append(self._nota_contexto(evento))
        observaciones.append(
            DescriptorEvidencia.texto_completo(tipo_codigo_original, evento.get("evidencia"))
        )

        tipo_final_codigo = self._tipo_final(tipo_codigo_original, resultado_yolo, evento, observaciones)

        gps = evento.get("gps") or {}
        latitud = gps.get("lat") if gps.get("fix") else None
        longitud = gps.get("lon") if gps.get("fix") else None

        return self._persistir_y_notificar(
            dispositivo=dispositivo, tipo_codigo=tipo_final_codigo, fecha_hora=t_deteccion_sensor,
            aceleracion_g=self._aceleracion(evento), nivel_audio_db=self._nivel_audio(evento),
            latitud=latitud, longitud=longitud, captura=captura, resultado_yolo=resultado_yolo,
            observacion=" ".join(p for p in observaciones if p),
            t_deteccion_sensor=t_deteccion_sensor, t_recepcion_servidor=t_recepcion_servidor,
            t_inicio_inferencia=t_inicio_inferencia, t_fin_inferencia=t_fin_inferencia,
            sin_reloj_sensor=sin_reloj_sensor, nivel_firmware=evento.get("nivel"),
            evidencia=evento.get("evidencia"),
        )

    def registrar_incidencia_visual(
        self, dispositivo: Dispositivo, captura: ResultadoCaptura, resultado_yolo: ResultadoDeteccion,
        t_deteccion_sensor, t_recepcion_servidor, t_inicio_inferencia, t_fin_inferencia,
        tipo_codigo: str = "PERSONA", clase_yolo: Optional[str] = None,
    ) -> Incidencia:
        """Usado por ``VigilanciaVisual``: la captura y la inferencia ya
        ocurrieron en el ciclo continuo de YOLO, aquí solo se persiste y se
        notifica.

        ``tipo_codigo`` viene de ``config.yolo.clases_vigiladas`` (``ARMA_FUEGO``,
        ``ARMA_BLANCA``, ``APERTURA_PUERTA``, ``VEHICULO_DANADO``,
        ``PERSONA_SOSPECHOSA``, ``PERSONA``); lo que se haga con él lo decide
        igual que siempre ``ReglasAccion`` a partir del catálogo.
        """
        vigilada = config.yolo.vigilada(clase_yolo) if clase_yolo else None
        contexto = (
            "con el vehículo ESTACIONADO y armado" if (vigilada and vigilada.solo_estacionado)
            else f"en modo {self._modo_actual(dispositivo.codigo) or 'desconocido'}"
        )
        detalle = f"clase '{clase_yolo}'" if clase_yolo else "detección visual"
        confianza = resultado_yolo.confianza_max
        if confianza is not None:
            detalle += f" al {confianza:.0%} de confianza"
        if resultado_yolo.resumen_clases:
            detalle += f" (detecciones: {resultado_yolo.resumen_clases})"

        observaciones = [
            f"Detectado por vigilancia visual continua (YOLO): {detalle}, {contexto}, "
            f"confirmado en {config.vision.frames_confirmacion} cuadro(s) seguido(s).",
            self._nota_imagen(captura),
        ]
        return self._persistir_y_notificar(
            dispositivo=dispositivo, tipo_codigo=tipo_codigo, fecha_hora=t_deteccion_sensor,
            aceleracion_g=None, nivel_audio_db=None, latitud=None, longitud=None,
            captura=captura, resultado_yolo=resultado_yolo,
            observacion=" ".join(p for p in observaciones if p),
            t_deteccion_sensor=t_deteccion_sensor, t_recepcion_servidor=t_recepcion_servidor,
            t_inicio_inferencia=t_inicio_inferencia, t_fin_inferencia=t_fin_inferencia,
            sin_reloj_sensor=False, nivel_firmware=None, evidencia=None,
        )

    def registrar_incidencia_persona(self, dispositivo: Dispositivo, captura: ResultadoCaptura,
                                     resultado_yolo: ResultadoDeteccion, **tiempos) -> Incidencia:
        """Alias histórico de ``registrar_incidencia_visual`` para la clase
        ``person`` (lo usaba ``VigilanciaVisual`` antes del modelo propio)."""
        return self.registrar_incidencia_visual(
            dispositivo=dispositivo, captura=captura, resultado_yolo=resultado_yolo,
            tipo_codigo="PERSONA", clase_yolo="person", **tiempos,
        )

    @staticmethod
    def _modo_actual(codigo: str) -> str:
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        return EstadoEnVivo.instancia().modo(codigo)

    # ------------------------------------------------------------------ #
    # Armado de la observación
    # ------------------------------------------------------------------ #
    @staticmethod
    def _nota_imagen(captura: ResultadoCaptura) -> str:
        """Registra de dónde salió la imagen (sección 5.5)."""
        if not captura.exito:
            return ""
        if captura.metodo == "stream":
            if captura.desfase_ms is not None:
                return f"Imagen del stream MJPEG, a {captura.desfase_ms:.0f} ms del instante del sensor."
            return "Imagen del stream MJPEG (cuadro más reciente)."
        if captura.metodo == "simulada":
            return "Imagen de la cámara simulada."
        return "Imagen pedida a /capture (sin stream disponible)."

    @staticmethod
    def _nota_contexto(evento: dict) -> str:
        """Modo del vehículo y armado en el momento del evento."""
        partes = []
        if evento.get("modo"):
            partes.append(f"modo {evento['modo']}")
        if evento.get("armado") is not None:
            partes.append("armado" if evento["armado"] else "desarmado")
        if evento.get("origen"):
            partes.append(f"vía {evento['origen']}")
        return f"Contexto del nodo: {', '.join(partes)}." if partes else ""

    def _tipo_final(self, tipo_original: str, resultado_yolo, evento: dict, observaciones: list) -> str:
        """YOLO reclasifica el evento según **lo que ve** junto al vehículo.

        Con el modelo propio de la tesis eso puede ser ``ARMA_FUEGO``,
        ``ARMA_BLANCA``, ``APERTURA_PUERTA``, ``VEHICULO_DANADO`` o
        ``PERSONA_SOSPECHOSA``; con el modelo genérico, ``PERSONA``. Se toma la
        detección de mayor confianza.

        Un CHOQUE no se reclasifica: es una emergencia por sí mismo y perdería
        su evidencia; en los demás casos lo que ve la cámara es más informativo
        que el disparo del sensor.
        """
        tipo_visual = resultado_yolo.tipo_evento_principal
        if tipo_visual is None:
            return tipo_original
        if tipo_original == "CHOQUE":
            observaciones.append(
                f"YOLO además detectó {resultado_yolo.num_detecciones} objeto(s) en la escena"
                f"{f': {resultado_yolo.resumen_clases}' if resultado_yolo.resumen_clases else ''}."
            )
            return tipo_original

        detalle = self._detalle_disparo(tipo_original, evento)
        observaciones.append(f"Disparado por sensor: {tipo_original}{detalle}.")
        observaciones.append(
            f"Reclasificado a {tipo_visual} por YOLO ({resultado_yolo.resumen_clases})."
        )
        return tipo_visual

    @staticmethod
    def _detalle_disparo(tipo_original: str, evento: dict) -> str:
        resumen = DescriptorEvidencia.resumen_corto(tipo_original, evento.get("evidencia"))
        if resumen:
            return f" ({resumen})"
        # Camino legado (v2.0.0): valor/umbral/unidad.
        if evento.get("valor") is not None:
            unidad = evento.get("unidad", "")
            return f" ({evento.get('valor')} {unidad} ≥ {evento.get('umbral')} {unidad})"
        return ""

    @staticmethod
    def _aceleracion(evento: dict):
        """``a_pico_g`` de la evidencia si existe; si no, ``aceleracion_g``."""
        evidencia = evento.get("evidencia") or {}
        return evidencia.get("a_pico_g") or evento.get("aceleracion_g")

    @staticmethod
    def _nivel_audio(evento: dict):
        """``spl_pico_db`` de la evidencia si existe; si no, ``nivel_audio_db``."""
        evidencia = evento.get("evidencia") or {}
        return evidencia.get("spl_pico_db") or evento.get("nivel_audio_db")

    # ------------------------------------------------------------------ #
    # Persistencia y notificación
    # ------------------------------------------------------------------ #
    def _persistir_y_notificar(
        self, *, dispositivo, tipo_codigo, fecha_hora, aceleracion_g, nivel_audio_db,
        latitud, longitud, captura, resultado_yolo, observacion,
        t_deteccion_sensor, t_recepcion_servidor, t_inicio_inferencia, t_fin_inferencia,
        sin_reloj_sensor, nivel_firmware=None, evidencia=None,
    ) -> Incidencia:
        tipo = self._obtener_tipo(tipo_codigo)
        accion = ReglasAccion.para(tipo)
        nota_nivel = ReglasAccion.validar_nivel_firmware(tipo, nivel_firmware)
        observacion = " ".join(p for p in (observacion, nota_nivel) if p).strip()

        nombre_archivo = self._nombre_archivo(fecha_hora, dispositivo.codigo)

        with transaction.atomic():
            incidencia = Incidencia(
                dispositivo=dispositivo, tipo=tipo, fecha_hora=fecha_hora,
                aceleracion_g=aceleracion_g, nivel_audio_db=nivel_audio_db,
                latitud=latitud, longitud=longitud, observacion=observacion,
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
        self._notificar_nueva(incidencia, resultado_yolo, accion)

        t_envio_telegram = t_confirmacion_telegram = None
        alerta_estado = None
        if self._debe_alertar(accion, resultado_yolo):
            t_envio_telegram = RelojServidor.ahora()
            resultado_difusion = self._difusor.difundir(
                incidencia, resultado_yolo, silenciosa=accion.silenciosa, evidencia=evidencia,
            )
            t_confirmacion_telegram = resultado_difusion.t_primera_confirmacion or RelojServidor.ahora()
            alerta_estado = "ENVIADO" if resultado_difusion.exito else "FALLIDO"
            if resultado_difusion.exito:
                incidencia.estado = "NOTIFICADA"
                incidencia.save(update_fields=["estado"])
        else:
            logger.info(
                "Incidencia #%s (%s, riesgo %s): %s.",
                incidencia.pk, tipo.codigo, accion.nivel_riesgo, accion.descripcion,
            )

        medicion = MedicionTiempo(
            incidencia=incidencia, t_deteccion_sensor=t_deteccion_sensor, t_recepcion_servidor=t_recepcion_servidor,
            t_inicio_inferencia=t_inicio_inferencia, t_fin_inferencia=t_fin_inferencia,
            t_registro_bd=t_registro_bd, t_envio_telegram=t_envio_telegram, t_confirmacion_telegram=t_confirmacion_telegram,
        )
        medicion._sin_reloj_sensor = sin_reloj_sensor
        medicion.save()

        NotificadorTiempoReal.incidencia_actualizada({
            "id": incidencia.pk, "estado": incidencia.estado, "alerta": alerta_estado,
            "accion": accion.descripcion,
            "tiempo_total_ms": medicion.tiempo_respuesta_total_ms,
        })

        if config.maps.geocodificar and incidencia.tiene_coordenadas:
            self._agregar_direccion_aproximada(incidencia)

        return incidencia

    @staticmethod
    def _debe_alertar(accion, resultado_yolo) -> bool:
        """Manda el catálogo (``nivel_riesgo``); BAJO nunca alerta.

        ``TELEGRAM_ALERTAR_SIN_PERSONA=False`` sigue valiendo como filtro
        adicional para los tipos que sí alertarían: en ese caso se exige que
        YOLO haya confirmado **algo** vigilado (un arma, una puerta abierta,
        daño, una persona), no solo la clase ``person``.
        """
        if not accion.alertar:
            return False
        return config.telegram.enviar_sin_persona or resultado_yolo.hay_alerta

    def _agregar_direccion_aproximada(self, incidencia: Incidencia) -> None:
        try:
            direccion = self._mapas.direccion_aproximada(float(incidencia.latitud), float(incidencia.longitud))
            if direccion:
                incidencia.observacion = f"{incidencia.observacion} Dirección aprox.: {direccion}.".strip()
                incidencia.save(update_fields=["observacion"])
        except Exception:
            logger.exception("Falló la geocodificación inversa (no crítico).")

    @staticmethod
    def _obtener_tipo(tipo_codigo: str) -> TipoEvento:
        """El ``TipoEvento`` del catálogo, sembrándolo si falta.

        Si el catálogo no se ha vuelto a sembrar después de cambiar de modelo,
        los tipos de las clases nuevas no existen y antes esto reventaba con
        ``DoesNotExist`` dentro del hilo de la vigilancia: la incidencia se
        perdía en silencio y nunca salía la alerta. Se crea con el nivel de
        riesgo del catálogo de ``config.py`` y se avisa en el log; cambiar el
        nivel después desde la página "Tipos de evento" sigue mandando.
        """
        tipo = TipoEvento.objects.filter(codigo=tipo_codigo).first()
        if tipo is not None:
            return tipo

        vigilada = next(
            (v for v in config.yolo.clases_vigiladas if v.tipo_evento == tipo_codigo), None,
        )
        tipo = TipoEvento.objects.create(
            codigo=tipo_codigo,
            nombre=vigilada.nombre if vigilada else tipo_codigo.replace("_", " ").capitalize(),
            nivel_riesgo=vigilada.nivel_riesgo if vigilada else "ALTO",
            descripcion=vigilada.descripcion if vigilada else "",
        )
        logger.warning(
            "El tipo de evento '%s' no estaba en el catálogo; se creó con riesgo %s. "
            "Ejecuta 'python manage.py sembrar_catalogos' para dejarlo sembrado.",
            tipo_codigo, tipo.nivel_riesgo,
        )
        return tipo

    @staticmethod
    def _nombre_archivo(fecha_hora, codigo: str) -> str:
        return f"evt_{fecha_hora:%Y%m%d_%H%M%S}_{fecha_hora.microsecond // 1000:03d}_{codigo}.jpg"

    @staticmethod
    def _notificar_nueva(incidencia: Incidencia, resultado_yolo: ResultadoDeteccion, accion) -> None:
        NotificadorTiempoReal.incidencia_nueva({
            "id": incidencia.pk, "tipo": incidencia.tipo.codigo, "nombre_tipo": incidencia.tipo.nombre,
            "riesgo": incidencia.tipo.nivel_riesgo, "placa": incidencia.dispositivo.vehiculo.placa,
            "fecha_hora": incidencia.fecha_hora.isoformat(),
            "accion": accion.descripcion,
            "miniatura": (
                incidencia.imagen_anotada.url if incidencia.imagen_anotada
                else (incidencia.imagen.url if incidencia.imagen else None)
            ),
            "lat": float(incidencia.latitud) if incidencia.latitud is not None else None,
            "lon": float(incidencia.longitud) if incidencia.longitud is not None else None,
            "personas": resultado_yolo.num_detecciones, "confianza_max": resultado_yolo.confianza_max,
            "clases": resultado_yolo.clases_detectadas,
        })
