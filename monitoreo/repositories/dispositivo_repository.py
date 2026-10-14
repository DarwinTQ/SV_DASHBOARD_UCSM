"""Consultas ORM reutilizables sobre Dispositivo / EstadoDispositivo."""
from __future__ import annotations

from django.utils import timezone

from config import config
from monitoreo.models import Dispositivo, EstadoDispositivo


class DispositivoRepository:
    def todos(self):
        return Dispositivo.objects.select_related("vehiculo__conductor").order_by("codigo")

    def buscar_por_codigo(self, codigo: str) -> Dispositivo | None:
        return Dispositivo.objects.select_related("vehiculo__conductor").filter(codigo=codigo).first()

    def en_linea(self, dispositivo: Dispositivo) -> bool:
        if dispositivo.ultimo_contacto is None:
            return False
        return (timezone.now() - dispositivo.ultimo_contacto).total_seconds() <= config.esp32.timeout_offline_s

    def historial_estados(self, dispositivo: Dispositivo, limite: int = 100):
        return EstadoDispositivo.objects.filter(dispositivo=dispositivo).order_by("-fecha_hora")[:limite]

    def disponibilidad_pct(self, dispositivo: Dispositivo, desde=None, hasta=None) -> float | None:
        qs = EstadoDispositivo.objects.filter(dispositivo=dispositivo)
        if desde:
            qs = qs.filter(fecha_hora__gte=desde)
        if hasta:
            qs = qs.filter(fecha_hora__lte=hasta)
        total = qs.count()
        if not total:
            return None
        en_linea = qs.filter(en_linea=True).count()
        return round(en_linea / total * 100, 2)
