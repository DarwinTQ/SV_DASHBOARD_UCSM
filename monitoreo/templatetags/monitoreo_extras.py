"""Filtros y tags de plantilla propios de la app monitoreo."""
import json

from django import template
from django.urls import reverse
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def to_json(valor):
    """Serializa una lista/dict de Python a JSON válido para incrustar en <script>."""
    return mark_safe(json.dumps(valor, default=str))


@register.simple_tag(takes_context=True)
def enlace_activo(context, nombre_url: str) -> str:
    """Devuelve "activo" si la ruta actual pertenece a la sección del enlace."""
    request = context.get("request")
    if request is None:
        return ""
    try:
        base = reverse(nombre_url)
    except Exception:
        return ""

    if nombre_url == "monitoreo:incidencia_lista" and request.path.startswith("/incidencias/mapa"):
        return ""
    if base == "/":
        return "activo" if request.path == "/" else ""
    return "activo" if request.path.startswith(base) else ""


@register.filter
def porcentaje(valor, decimales=1):
    if valor is None:
        return "—"
    return f"{valor:.{decimales}f}%"


@register.filter
def ms(valor, decimales=1):
    if valor is None:
        return "—"
    return f"{valor:.{decimales}f} ms"
