"""Consultas ORM reutilizables sobre Incidencia."""
from __future__ import annotations

from django.db.models import Count, Q
from django.utils import timezone

from monitoreo.models import AlertaTelegram, Incidencia


class IncidenciaRepository:
    """Centraliza las consultas de incidencias usadas por el panel y los CRUD."""

    def base_queryset(self):
        return Incidencia.objects.select_related("dispositivo__vehiculo__conductor", "tipo")

    def filtrar(self, *, desde=None, hasta=None, tipo=None, riesgo=None, estado=None,
                validacion=None, placa=None, dispositivo_id=None) -> "QuerySet":
        qs = self.base_queryset()
        if desde:
            qs = qs.filter(fecha_hora__gte=desde)
        if hasta:
            qs = qs.filter(fecha_hora__lte=hasta)
        if tipo:
            qs = qs.filter(tipo__codigo=tipo)
        if riesgo:
            qs = qs.filter(tipo__nivel_riesgo=riesgo)
        if estado:
            qs = qs.filter(estado=estado)
        if validacion:
            qs = qs.filter(validacion=validacion)
        if placa:
            qs = qs.filter(dispositivo__vehiculo__placa__icontains=placa)
        if dispositivo_id:
            qs = qs.filter(dispositivo_id=dispositivo_id)
        return qs

    def recientes(self, cantidad: int = 10):
        return self.base_queryset().order_by("-fecha_hora")[:cantidad]

    def con_coordenadas(self, **filtros):
        return self.filtrar(**filtros).filter(latitud__isnull=False, longitud__isnull=False)

    def contador_sin_validar(self) -> int:
        return Incidencia.objects.filter(validacion__isnull=True).count()

    def kpis_hoy(self) -> dict:
        inicio_hoy = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        incidencias_hoy = Incidencia.objects.filter(fecha_hora__gte=inicio_hoy)
        alertas_hoy = AlertaTelegram.objects.filter(incidencia__fecha_hora__gte=inicio_hoy)

        total_alertas = alertas_hoy.count()
        enviadas = alertas_hoy.filter(estado_envio="ENVIADO").count()
        tasa_exito = round(enviadas / total_alertas * 100, 1) if total_alertas else None

        tiempos = incidencias_hoy.filter(tiempos__tiempo_respuesta_total_ms__isnull=False).values_list(
            "tiempos__tiempo_respuesta_total_ms", flat=True,
        )
        tiempos = list(tiempos)
        promedio_ms = round(sum(tiempos) / len(tiempos), 1) if tiempos else None

        return {
            "incidencias_hoy": incidencias_hoy.count(),
            "alertas_hoy": total_alertas,
            "alertas_tasa_exito": tasa_exito,
            "tiempo_respuesta_promedio_ms": promedio_ms,
        }

    def por_tipo(self, **filtros):
        return self.filtrar(**filtros).values("tipo__nombre").annotate(total=Count("id")).order_by("-total")

    def por_riesgo(self, **filtros):
        return self.filtrar(**filtros).values("tipo__nivel_riesgo").annotate(total=Count("id")).order_by("-total")
