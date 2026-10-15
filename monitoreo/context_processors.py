"""Variables disponibles en todos los templates."""
from config import config


def contexto_global(request):
    contador_sin_validar = None
    if request.user.is_authenticated:
        from monitoreo.repositories.incidencia_repository import IncidenciaRepository

        contador_sin_validar = IncidenciaRepository().contador_sin_validar()

    return {
        "url_publica": config.servidor.url_publica,
        "google_maps_api_key": config.maps.api_key,
        "mapa_zoom": config.maps.zoom,
        "contador_sin_validar": contador_sin_validar,
    }
