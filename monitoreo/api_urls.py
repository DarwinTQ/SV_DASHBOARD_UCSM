"""Rutas /api/v1/ — el firmware (sección 7) y el panel (sección 10)."""
from django.urls import path

from monitoreo.controllers.api.eventos_api import EventosApi
from monitoreo.controllers.api.panel_api import (
    DispositivoArmarApi, PanelEstadoApi, PanelIncidenciasRecientesApi, PanelMetricasApi,
)
from monitoreo.controllers.api.telemetria_api import TelemetriaApi

urlpatterns = [
    # Firmware (sección 7, contrato fijo)
    path("telemetria/", TelemetriaApi.as_view(), name="api_telemetria"),
    path("eventos/", EventosApi.as_view(), name="api_eventos"),

    # Panel en tiempo real (sección 10)
    path("panel/estado/", PanelEstadoApi.as_view(), name="api_panel_estado"),
    path("panel/incidencias/recientes/", PanelIncidenciasRecientesApi.as_view(), name="api_panel_incidencias_recientes"),
    path("panel/metricas/", PanelMetricasApi.as_view(), name="api_panel_metricas"),
    path("panel/dispositivos/<int:pk>/armar/", DispositivoArmarApi.as_view(), name="api_panel_dispositivo_armar"),
]
