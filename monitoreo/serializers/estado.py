"""Valida los tópicos de estado y control que llegan por MQTT (sección 2).

Son mensajes cortos pero críticos: ``estado`` (y su *Last Will*) es lo que
marca el nodo en línea o fuera de línea al instante, y ``sync/req`` es el que
alimenta ``t_deteccion_sensor`` de ``medicion_tiempo``.
"""
from rest_framework import serializers

MODOS_VEHICULO = ("EN_MARCHA", "ESTACIONADO")
FUENTES_MODO = ("INERCIAL", "CONTACTO", "FORZADO")


class EstadoNodoSerializer(serializers.Serializer):
    """``ucsm/v1/<codigo>/estado`` (retenido). El *Last Will* solo trae
    ``en_linea: false``, así que todo lo demás es opcional."""

    en_linea = serializers.BooleanField()
    codigo = serializers.CharField(max_length=20, required=False, allow_blank=True)
    firmware = serializers.CharField(max_length=10, required=False, allow_blank=True)
    ip = serializers.CharField(max_length=45, required=False, allow_blank=True)
    mac = serializers.CharField(max_length=17, required=False, allow_blank=True)
    rssi_dbm = serializers.IntegerField(min_value=-120, max_value=0, required=False, allow_null=True)
    boot_id = serializers.IntegerField(required=False, allow_null=True)
    armado = serializers.BooleanField(required=False, allow_null=True)
    modo = serializers.ChoiceField(choices=MODOS_VEHICULO, required=False, allow_null=True)


class ModoSerializer(serializers.Serializer):
    """``ucsm/v1/<codigo>/modo`` (retenido)."""

    modo = serializers.ChoiceField(choices=MODOS_VEHICULO)
    fuente = serializers.ChoiceField(choices=FUENTES_MODO, required=False, allow_null=True)
    ts_ms = serializers.IntegerField(required=False, allow_null=True)
    contacto = serializers.BooleanField(required=False, allow_null=True)


class SyncPeticionSerializer(serializers.Serializer):
    """``ucsm/v1/<codigo>/sync/req`` -> el servidor responde en ``sync/resp``."""

    id = serializers.IntegerField(min_value=0)
    t0 = serializers.IntegerField(min_value=0)


class EstadoCamaraSerializer(serializers.Serializer):
    """``ucsm/v1/<codigo>/cam/estado`` (retenido + *Last Will* de la ESP32-CAM)."""

    en_linea = serializers.BooleanField()
    ok = serializers.BooleanField(required=False, allow_null=True)
    codigo = serializers.CharField(max_length=30, required=False, allow_blank=True)
    codigo_nodo = serializers.CharField(max_length=20, required=False, allow_blank=True)
    firmware = serializers.CharField(max_length=10, required=False, allow_blank=True)
    mac = serializers.CharField(max_length=17, required=False, allow_blank=True)
    ip = serializers.CharField(max_length=45, required=False, allow_blank=True)
    rssi_dbm = serializers.IntegerField(min_value=-120, max_value=0, required=False, allow_null=True)
    uptime_ms = serializers.IntegerField(required=False, allow_null=True)
    ram_libre_kb = serializers.IntegerField(required=False, allow_null=True)
    psram_libre_kb = serializers.IntegerField(required=False, allow_null=True)
    resolucion = serializers.CharField(max_length=10, required=False, allow_blank=True)
    calidad_jpeg = serializers.IntegerField(required=False, allow_null=True)
    fps_max = serializers.IntegerField(required=False, allow_null=True)
    fps_real = serializers.FloatField(required=False, allow_null=True)
    clientes_stream = serializers.IntegerField(required=False, allow_null=True)
    flash = serializers.BooleanField(required=False, allow_null=True)
    capturas = serializers.IntegerField(required=False, allow_null=True)
    cuadros_stream = serializers.IntegerField(required=False, allow_null=True)
    fallos = serializers.IntegerField(required=False, allow_null=True)
    url_stream = serializers.CharField(max_length=120, required=False, allow_blank=True)
