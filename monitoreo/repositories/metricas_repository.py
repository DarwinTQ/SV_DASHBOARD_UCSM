"""Consultas de agregación para resultados/métricas (se completa en la Fase 6)."""
from monitoreo.models import Incidencia


class MetricasRepository:
    def incidencias_en_rango(self, desde=None, hasta=None, dispositivo_id=None, tipo=None):
        qs = Incidencia.objects.select_related("tiempos", "dispositivo")
        if desde:
            qs = qs.filter(fecha_hora__gte=desde)
        if hasta:
            qs = qs.filter(fecha_hora__lte=hasta)
        if dispositivo_id:
            qs = qs.filter(dispositivo_id=dispositivo_id)
        if tipo:
            qs = qs.filter(tipo__codigo=tipo)
        return qs
