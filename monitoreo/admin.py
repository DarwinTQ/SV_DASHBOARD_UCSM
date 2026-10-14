from django.contrib import admin

from monitoreo.models import (
    AccesoTelegram,
    AlertaTelegram,
    Conductor,
    DeteccionYolo,
    Dispositivo,
    EstadoDispositivo,
    Incidencia,
    MedicionTiempo,
    TipoEvento,
    Vehiculo,
)


@admin.register(Conductor)
class ConductorAdmin(admin.ModelAdmin):
    list_display = ("dni", "nombres", "apellidos", "telefono", "tiene_telegram")
    search_fields = ("dni", "nombres", "apellidos")


@admin.register(Vehiculo)
class VehiculoAdmin(admin.ModelAdmin):
    list_display = ("placa", "marca", "modelo", "anio", "color", "conductor", "activo")
    search_fields = ("placa", "marca", "modelo")
    list_filter = ("tipo", "activo")


@admin.register(Dispositivo)
class DispositivoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "vehiculo", "estado", "ip", "ultimo_contacto")
    list_filter = ("estado",)
    search_fields = ("codigo",)


@admin.register(EstadoDispositivo)
class EstadoDispositivoAdmin(admin.ModelAdmin):
    list_display = ("dispositivo", "fecha_hora", "en_linea", "rssi_wifi_dbm", "voltaje_v")
    list_filter = ("en_linea", "dispositivo")


@admin.register(TipoEvento)
class TipoEventoAdmin(admin.ModelAdmin):
    list_display = ("codigo", "nombre", "nivel_riesgo")


class DeteccionYoloInline(admin.TabularInline):
    model = DeteccionYolo
    extra = 0


class MedicionTiempoInline(admin.StackedInline):
    model = MedicionTiempo
    extra = 0


@admin.register(Incidencia)
class IncidenciaAdmin(admin.ModelAdmin):
    list_display = ("id", "tipo", "dispositivo", "fecha_hora", "estado", "validacion")
    list_filter = ("tipo", "estado", "validacion", "dispositivo")
    date_hierarchy = "fecha_hora"
    inlines = [DeteccionYoloInline, MedicionTiempoInline]


@admin.register(AlertaTelegram)
class AlertaTelegramAdmin(admin.ModelAdmin):
    list_display = ("id", "incidencia", "conductor", "estado_envio", "intentos", "fecha_envio")
    list_filter = ("estado_envio",)


@admin.register(AccesoTelegram)
class AccesoTelegramAdmin(admin.ModelAdmin):
    list_display = ("nombres", "vehiculo", "rol", "vinculado", "puede_controlar", "recibe_alertas", "activo")
    list_filter = ("rol", "activo", "puede_controlar", "recibe_alertas")
    search_fields = ("nombres", "chat_id", "usuario_telegram", "vehiculo__placa")
