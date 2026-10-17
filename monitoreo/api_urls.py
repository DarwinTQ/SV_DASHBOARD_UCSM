"""Rutas /api/v1/ — el firmware (sección 7) y el panel (sección 10).

Las dos rutas del firmware se **mantienen** como respaldo: el camino normal
desde la v3.0.0 es MQTT (ver ``PuenteMQTT``), pero si el broker no está
disponible el nodo puede seguir hablando HTTP con el mismo contrato.
"""
from django.urls import path

from monitoreo.controllers.api.eventos_api import EventosApi
from monitoreo.controllers.api.panel_api import (
    CamaraComandoApi, DispositivoArmarApi, NodoComandoApi, PanelEstadoApi,
    PanelIncidenciasRecientesApi, PanelMetricasApi, PanelRastroApi, PanelRastroLimpiarApi,
)
from monitoreo.controllers.api.telemetria_api import TelemetriaApi

urlpatterns = [
    # Firmware (sección 7, contrato fijo) — respaldo del canal MQTT
    path("telemetria/", TelemetriaApi.as_view(), name="api_telemetria"),
    path("eventos/", EventosApi.as_view(), name="api_eventos"),

    # Panel en tiempo real (sección 10)
    path("panel/estado/", PanelEstadoApi.as_view(), name="api_panel_estado"),
    path("panel/incidencias/recientes/", PanelIncidenciasRecientesApi.as_view(), name="api_panel_incidencias_recientes"),
    path("panel/metricas/", PanelMetricasApi.as_view(), name="api_panel_metricas"),
    path("panel/dispositivos/<int:pk>/armar/", DispositivoArmarApi.as_view(), name="api_panel_dispositivo_armar"),

    # Mapa en vivo (sección 5.6)
    path("panel/rastro/<str:codigo>/", PanelRastroApi.as_view(), name="api_panel_rastro"),
    path("panel/rastro/<str:codigo>/limpiar/", PanelRastroLimpiarApi.as_view(), name="api_panel_rastro_limpiar"),

    # Control por MQTT (secciones 5.3 y 5.5)
    path("panel/dispositivos/<int:pk>/comando/", NodoComandoApi.as_view(), name="api_panel_nodo_comando"),
    path("panel/camara/<str:codigo>/comando/", CamaraComandoApi.as_view(), name="api_panel_camara_comando"),
]
