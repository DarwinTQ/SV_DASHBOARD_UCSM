"""Rutas web de la app monitoreo."""
from django.urls import path

from monitoreo.controllers.web.acceso_telegram_controller import (
    AccesoTelegramInvitarController, AccesoTelegramPermisoController, AccesoTelegramRevocarController,
)
from monitoreo.controllers.web.alerta_controller import AlertaListController, AlertaReintentarController
from monitoreo.controllers.web.auth_controller import AuthLoginController, AuthLogoutController
from monitoreo.controllers.web.conductor_controller import (
    ConductorCreateController, ConductorDeleteController, ConductorDetalleController,
    ConductorListController, ConductorUpdateController,
)
from monitoreo.controllers.web.configuracion_controller import (
    ConfiguracionController, ProbarCamaraController, ProbarTelegramController, ProbarYoloController,
)
from monitoreo.controllers.web.dispositivo_controller import (
    DispositivoArmarController, DispositivoCreateController, DispositivoDeleteController,
    DispositivoDetalleController, DispositivoListController, DispositivoProbarCamaraController,
    DispositivoUpdateController,
)
from monitoreo.controllers.web.incidencia_controller import (
    IncidenciaDetalleController, IncidenciaListController, IncidenciaMapaController,
)
from monitoreo.controllers.web.media_controller import MediaController
from monitoreo.controllers.web.panel_controller import PanelController
from monitoreo.controllers.web.resultados_controller import ExportarExcelController, ResultadosController
from monitoreo.controllers.web.tipo_evento_controller import (
    TipoEventoCreateController, TipoEventoDeleteController, TipoEventoDetalleController,
    TipoEventoListController, TipoEventoUpdateController,
)
from monitoreo.controllers.web.vehiculo_controller import (
    VehiculoCreateController, VehiculoDeleteController, VehiculoDetalleController,
    VehiculoListController, VehiculoUpdateController,
)

app_name = "monitoreo"

urlpatterns = [
    # Autenticación
    path("login/", AuthLoginController.as_view(), name="login"),
    path("logout/", AuthLogoutController.as_view(), name="logout"),

    # Panel en tiempo real
    path("", PanelController.as_view(), name="panel"),

    # Incidencias
    path("incidencias/", IncidenciaListController.as_view(), name="incidencia_lista"),
    path("incidencias/mapa/", IncidenciaMapaController.as_view(), name="incidencia_mapa"),
    path("incidencias/<int:pk>/", IncidenciaDetalleController.as_view(), name="incidencia_detalle"),

    # Conductores
    path("conductores/", ConductorListController.as_view(), name="conductor_lista"),
    path("conductores/nuevo/", ConductorCreateController.as_view(), name="conductor_crear"),
    path("conductores/<int:pk>/", ConductorDetalleController.as_view(), name="conductor_detalle"),
    path("conductores/<int:pk>/editar/", ConductorUpdateController.as_view(), name="conductor_editar"),
    path("conductores/<int:pk>/eliminar/", ConductorDeleteController.as_view(), name="conductor_eliminar"),

    # Vehículos
    path("vehiculos/", VehiculoListController.as_view(), name="vehiculo_lista"),
    path("vehiculos/nuevo/", VehiculoCreateController.as_view(), name="vehiculo_crear"),
    path("vehiculos/<int:pk>/", VehiculoDetalleController.as_view(), name="vehiculo_detalle"),
    path("vehiculos/<int:pk>/editar/", VehiculoUpdateController.as_view(), name="vehiculo_editar"),
    path("vehiculos/<int:pk>/eliminar/", VehiculoDeleteController.as_view(), name="vehiculo_eliminar"),
    path("vehiculos/<int:pk>/invitar-telegram/", AccesoTelegramInvitarController.as_view(), name="vehiculo_invitar_telegram"),

    # Accesos de Telegram (propietario/familiares)
    path("accesos-telegram/<int:pk>/revocar/", AccesoTelegramRevocarController.as_view(), name="acceso_telegram_revocar"),
    path("accesos-telegram/<int:pk>/permiso/", AccesoTelegramPermisoController.as_view(), name="acceso_telegram_permiso"),

    # Dispositivos
    path("dispositivos/", DispositivoListController.as_view(), name="dispositivo_lista"),
    path("dispositivos/nuevo/", DispositivoCreateController.as_view(), name="dispositivo_crear"),
    path("dispositivos/<int:pk>/", DispositivoDetalleController.as_view(), name="dispositivo_detalle"),
    path("dispositivos/<int:pk>/editar/", DispositivoUpdateController.as_view(), name="dispositivo_editar"),
    path("dispositivos/<int:pk>/eliminar/", DispositivoDeleteController.as_view(), name="dispositivo_eliminar"),
    path("dispositivos/<int:pk>/armar/", DispositivoArmarController.as_view(), name="dispositivo_armar"),
    path("dispositivos/<int:pk>/probar-camara/", DispositivoProbarCamaraController.as_view(), name="dispositivo_probar_camara"),

    # Tipos de evento
    path("tipos-evento/", TipoEventoListController.as_view(), name="tipo_evento_lista"),
    path("tipos-evento/nuevo/", TipoEventoCreateController.as_view(), name="tipo_evento_crear"),
    path("tipos-evento/<int:pk>/", TipoEventoDetalleController.as_view(), name="tipo_evento_detalle"),
    path("tipos-evento/<int:pk>/editar/", TipoEventoUpdateController.as_view(), name="tipo_evento_editar"),
    path("tipos-evento/<int:pk>/eliminar/", TipoEventoDeleteController.as_view(), name="tipo_evento_eliminar"),

    # Alertas Telegram
    path("alertas/", AlertaListController.as_view(), name="alerta_lista"),
    path("alertas/<int:pk>/reintentar/", AlertaReintentarController.as_view(), name="alerta_reintentar"),

    # Resultados y métricas
    path("resultados/", ResultadosController.as_view(), name="resultados"),
    path("resultados/exportar-excel/", ExportarExcelController.as_view(), name="resultados_exportar_excel"),

    # Configuración
    path("configuracion/", ConfiguracionController.as_view(), name="configuracion"),
    path("configuracion/probar-telegram/", ProbarTelegramController.as_view(), name="configuracion_probar_telegram"),
    path("configuracion/probar-camara/", ProbarCamaraController.as_view(), name="configuracion_probar_camara"),
    path("configuracion/probar-yolo/", ProbarYoloController.as_view(), name="configuracion_probar_yolo"),

    # Media protegida (solo usuarios autenticados)
    path("media/<path:ruta>", MediaController.as_view(), name="media_protegida"),
]
