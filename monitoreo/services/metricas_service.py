"""Estadísticas para el Capítulo V de la tesis (sección 8.6)."""
from __future__ import annotations

import statistics
from typing import Optional

from django.db.models import Avg, Count
from django.db.models.functions import ExtractHour, TruncDate

from monitoreo.models import AlertaTelegram
from monitoreo.repositories.metricas_repository import MetricasRepository
from monitoreo.services.disponibilidad_service import DisponibilidadService

CAMPOS_TIEMPO = [
    "latencia_red_ms", "tiempo_inferencia_ms", "tiempo_bd_ms",
    "latencia_telegram_ms", "tiempo_respuesta_total_ms", "fps",
]


class MetricasService:
    """Agrupa estadísticas descriptivas, matriz de confusión, disponibilidad y alertas."""

    def __init__(self, repositorio=None, disponibilidad_service=None):
        self._repo = repositorio or MetricasRepository()
        self._disponibilidad = disponibilidad_service or DisponibilidadService()

    # ------------------------------------------------------------------ #
    # Estadísticas de tiempos
    # ------------------------------------------------------------------ #
    def estadisticas_tiempos(self, desde=None, hasta=None, dispositivo_id=None, tipo=None) -> dict:
        incidencias = list(self._repo.incidencias_en_rango(desde, hasta, dispositivo_id, tipo))
        resultado = {}
        for campo in CAMPOS_TIEMPO:
            valores = [
                getattr(inc.tiempos, campo) for inc in incidencias
                if hasattr(inc, "tiempos") and getattr(inc.tiempos, campo) is not None
            ]
            resultado[campo] = self._estadistica_descriptiva(valores)

        capturas = [
            inc.tiempos.tiempo_captura_ms for inc in incidencias
            if hasattr(inc, "tiempos") and inc.tiempos.tiempo_captura_ms is not None
        ]
        resultado["tiempo_captura_ms"] = self._estadistica_descriptiva(capturas)
        return resultado

    @staticmethod
    def _percentil(ordenados: list, p: float) -> Optional[float]:
        if not ordenados:
            return None
        if len(ordenados) == 1:
            return ordenados[0]
        posicion = (p / 100) * (len(ordenados) - 1)
        inferior = int(posicion)
        superior = min(inferior + 1, len(ordenados) - 1)
        fraccion = posicion - inferior
        return ordenados[inferior] + (ordenados[superior] - ordenados[inferior]) * fraccion

    @classmethod
    def _estadistica_descriptiva(cls, valores: list) -> dict:
        n = len(valores)
        if n == 0:
            return {"n": 0, "media": None, "desviacion": None, "minimo": None, "maximo": None, "p50": None, "p95": None}
        ordenados = sorted(valores)
        return {
            "n": n,
            "media": round(statistics.fmean(valores), 2),
            "desviacion": round(statistics.pstdev(valores), 2) if n > 1 else 0.0,
            "minimo": round(min(valores), 2),
            "maximo": round(max(valores), 2),
            "p50": round(statistics.median(valores), 2),
            "p95": round(cls._percentil(ordenados, 95), 2),
        }

    # ------------------------------------------------------------------ #
    # Matriz de confusión
    # ------------------------------------------------------------------ #
    def matriz_confusion(self, desde=None, hasta=None, dispositivo_id=None, tipo=None) -> dict:
        incidencias = self._repo.incidencias_en_rango(desde, hasta, dispositivo_id, tipo)
        conteo = {"VP": 0, "FP": 0, "FN": 0, "VN": 0}
        sin_validar = 0
        for inc in incidencias:
            if inc.validacion in conteo:
                conteo[inc.validacion] += 1
            else:
                sin_validar += 1

        vp, fp, fn, vn = conteo["VP"], conteo["FP"], conteo["FN"], conteo["VN"]
        total_validadas = vp + fp + fn + vn
        precision = vp / (vp + fp) if (vp + fp) else None
        exhaustividad = vp / (vp + fn) if (vp + fn) else None
        f1 = (
            2 * precision * exhaustividad / (precision + exhaustividad)
            if precision and exhaustividad and (precision + exhaustividad) else None
        )
        exactitud = (vp + vn) / total_validadas if total_validadas else None

        return {
            "conteo": conteo,
            "precision": round(precision, 4) if precision is not None else None,
            "exhaustividad": round(exhaustividad, 4) if exhaustividad is not None else None,
            "f1": round(f1, 4) if f1 is not None else None,
            "exactitud": round(exactitud, 4) if exactitud is not None else None,
            "sin_validar": sin_validar,
            "total": total_validadas + sin_validar,
        }

    # ------------------------------------------------------------------ #
    # Disponibilidad y alertas
    # ------------------------------------------------------------------ #
    def disponibilidad(self, desde=None, hasta=None, dispositivo_id=None) -> dict:
        return self._disponibilidad.calcular(desde, hasta, dispositivo_id)

    def resumen_alertas(self, desde=None, hasta=None, dispositivo_id=None) -> dict:
        qs = AlertaTelegram.objects.all()
        if desde:
            qs = qs.filter(incidencia__fecha_hora__gte=desde)
        if hasta:
            qs = qs.filter(incidencia__fecha_hora__lte=hasta)
        if dispositivo_id:
            qs = qs.filter(incidencia__dispositivo_id=dispositivo_id)

        total = qs.count()
        enviadas = qs.filter(estado_envio="ENVIADO").count()
        fallidas = qs.filter(estado_envio="FALLIDO").count()
        intentos_prom = qs.aggregate(promedio=Avg("intentos"))["promedio"]
        return {
            "total": total, "enviadas": enviadas, "fallidas": fallidas,
            "tasa_exito": round(enviadas / total * 100, 2) if total else None,
            "intentos_promedio": round(intentos_prom, 2) if intentos_prom is not None else None,
        }

    # ------------------------------------------------------------------ #
    # Entrega de alertas por destinatario (sección 8, "Entrega de alertas")
    # ------------------------------------------------------------------ #
    def entrega_alertas(self, desde=None, hasta=None, dispositivo_id=None) -> list:
        from monitoreo.models import AlertaTelegram

        qs = AlertaTelegram.objects.select_related("incidencia__tiempos", "incidencia__dispositivo")
        if desde:
            qs = qs.filter(incidencia__fecha_hora__gte=desde)
        if hasta:
            qs = qs.filter(incidencia__fecha_hora__lte=hasta)
        if dispositivo_id:
            qs = qs.filter(incidencia__dispositivo_id=dispositivo_id)

        detalle = []
        for alerta in qs.order_by("-incidencia__fecha_hora")[:200]:
            tiempos = getattr(alerta.incidencia, "tiempos", None)
            latencia_ms = None
            if tiempos and tiempos.t_envio_telegram and alerta.fecha_envio:
                latencia_ms = round((alerta.fecha_envio - tiempos.t_envio_telegram).total_seconds() * 1000, 1)
            detalle.append({
                "id": alerta.pk, "incidencia_id": alerta.incidencia_id,
                "destinatario": alerta.destinatario_nombre, "estado": alerta.estado_envio,
                "latencia_ms": latencia_ms,
            })
        return detalle

    def resumen_entrega_alertas(self, desde=None, hasta=None, dispositivo_id=None) -> dict:
        detalle = self.entrega_alertas(desde, hasta, dispositivo_id)
        total = len(detalle)
        entregadas = [d for d in detalle if d["estado"] == "ENVIADO"]
        latencias = [d["latencia_ms"] for d in entregadas if d["latencia_ms"] is not None]
        return {
            "total": total, "entregadas": len(entregadas),
            "pct_entregadas": round(len(entregadas) / total * 100, 1) if total else None,
            "latencia_promedio_ms": round(statistics.fmean(latencias), 1) if latencias else None,
            "detalle": detalle[:50],
        }

    # ------------------------------------------------------------------ #
    # Distribuciones
    # ------------------------------------------------------------------ #
    def incidencias_por_tipo(self, desde=None, hasta=None, dispositivo_id=None):
        qs = self._repo.incidencias_en_rango(desde, hasta, dispositivo_id)
        return list(qs.values("tipo__nombre").annotate(total=Count("id")).order_by("-total"))

    def incidencias_por_riesgo(self, desde=None, hasta=None, dispositivo_id=None):
        qs = self._repo.incidencias_en_rango(desde, hasta, dispositivo_id)
        return list(qs.values("tipo__nivel_riesgo").annotate(total=Count("id")).order_by("-total"))

    def incidencias_por_dia(self, desde=None, hasta=None, dispositivo_id=None):
        qs = self._repo.incidencias_en_rango(desde, hasta, dispositivo_id)
        return list(qs.annotate(dia=TruncDate("fecha_hora")).values("dia").annotate(total=Count("id")).order_by("dia"))

    def incidencias_por_hora(self, desde=None, hasta=None, dispositivo_id=None):
        qs = self._repo.incidencias_en_rango(desde, hasta, dispositivo_id)
        return list(qs.annotate(hora=ExtractHour("fecha_hora")).values("hora").annotate(total=Count("id")).order_by("hora"))

    def resultados_completos(self, desde=None, hasta=None, dispositivo_id=None, tipo=None) -> dict:
        return {
            "tiempos": self.estadisticas_tiempos(desde, hasta, dispositivo_id, tipo),
            "matriz_confusion": self.matriz_confusion(desde, hasta, dispositivo_id, tipo),
            "disponibilidad": self.disponibilidad(desde, hasta, dispositivo_id),
            "alertas": self.resumen_alertas(desde, hasta, dispositivo_id),
            "por_tipo": self.incidencias_por_tipo(desde, hasta, dispositivo_id),
            "por_riesgo": self.incidencias_por_riesgo(desde, hasta, dispositivo_id),
            "por_dia": self.incidencias_por_dia(desde, hasta, dispositivo_id),
            "por_hora": self.incidencias_por_hora(desde, hasta, dispositivo_id),
        }
