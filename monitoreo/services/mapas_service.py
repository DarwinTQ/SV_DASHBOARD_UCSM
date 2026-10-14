"""Enlaces de mapas y geocodificación inversa opcional, sección 8.5."""
from __future__ import annotations

import logging
from typing import Optional

import requests

from config import config

logger = logging.getLogger("monitoreo.mapas")


class MapasService:
    """Google Maps si hay clave configurada; si no, el frontend usa Leaflet/OSM."""

    def url_google_maps(self, lat: float, lon: float) -> str:
        return f"https://maps.google.com/?q={lat},{lon}"

    def url_static_map(self, lat: float, lon: float) -> Optional[str]:
        if not config.maps.api_key:
            return None
        return (
            "https://maps.googleapis.com/maps/api/staticmap"
            f"?center={lat},{lon}&zoom={config.maps.zoom}&size=640x320"
            f"&markers=color:0x0cb967%7C{lat},{lon}&key={config.maps.api_key}"
        )

    def direccion_aproximada(self, lat: float, lon: float) -> Optional[str]:
        """Geocodificación inversa opcional. Nunca se llama antes de enviar la alerta."""
        if not config.maps.api_key or not config.maps.geocodificar:
            return None
        try:
            resp = requests.get(
                "https://maps.googleapis.com/maps/api/geocode/json",
                params={"latlng": f"{lat},{lon}", "key": config.maps.api_key, "language": "es"},
                timeout=5,
            )
            resp.raise_for_status()
            resultados = resp.json().get("results") or []
            return resultados[0]["formatted_address"] if resultados else None
        except requests.RequestException:
            logger.exception("Falló la geocodificación inversa de Google (no crítico).")
            return None
