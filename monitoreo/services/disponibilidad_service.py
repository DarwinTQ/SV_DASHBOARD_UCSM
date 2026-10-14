"""Cálculo de disponibilidad del sistema por muestreo (sección 8.6)."""
from __future__ import annotations

from monitoreo.models import EstadoDispositivo


class DisponibilidadService:
    """Disponibilidad (%) = filas en_linea=True / total de filas del periodo × 100."""

    def calcular(self, desde=None, hasta=None, dispositivo_id=None) -> dict:
        qs = EstadoDispositivo.objects.all()
        if desde:
            qs = qs.filter(fecha_hora__gte=desde)
        if hasta:
            qs = qs.filter(fecha_hora__lte=hasta)
        if dispositivo_id:
            qs = qs.filter(dispositivo_id=dispositivo_id)

        total = qs.count()
        en_linea = qs.filter(en_linea=True).count()
        return {
            "total_muestras": total,
            "muestras_en_linea": en_linea,
            "disponibilidad_pct": round(en_linea / total * 100, 2) if total else None,
        }
