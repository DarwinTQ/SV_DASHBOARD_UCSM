"""Serializadores anidados reutilizados por telemetría y eventos."""
from rest_framework import serializers


class WifiSerializer(serializers.Serializer):
    rssi_dbm = serializers.IntegerField(min_value=-120, max_value=0, required=False, allow_null=True)


class SistemaSerializer(serializers.Serializer):
    ram_libre_kb = serializers.IntegerField(required=False, allow_null=True)
    temp_c = serializers.FloatField(required=False, allow_null=True)


class GpsSerializer(serializers.Serializer):
    """Bloque ``gps`` anidado en telemetría y eventos.

    ``rumbo_deg`` y ``motivo`` se agregaron en la v3.0.0: el primero viene de
    ``gps.course.deg()`` del NEO-6M (lo necesita la flecha del mapa) y el
    segundo explica por qué una posición con fix se descartó (``"hdop"``).
    """

    fix = serializers.BooleanField()
    sats = serializers.IntegerField()
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False, allow_null=True)
    lon = serializers.FloatField(min_value=-180, max_value=180, required=False, allow_null=True)
    vel_kmh = serializers.FloatField(required=False, allow_null=True)
    rumbo_deg = serializers.FloatField(min_value=0, max_value=360, required=False, allow_null=True)
    alt_m = serializers.FloatField(required=False, allow_null=True)
    hdop = serializers.FloatField(required=False, allow_null=True)
    motivo = serializers.CharField(max_length=20, required=False, allow_blank=True)


class GpsPosicionSerializer(serializers.Serializer):
    """Payload del tópico ``ucsm/v1/<codigo>/gps`` (5 Hz en marcha, sección 3.5).

    Va aparte de ``GpsSerializer`` porque es un mensaje autónomo: trae su propia
    marca de tiempo, la antigüedad del dato y el modo del vehículo.
    """

    ts_ms = serializers.IntegerField(required=False, allow_null=True)
    fix = serializers.BooleanField()
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False, allow_null=True)
    lon = serializers.FloatField(min_value=-180, max_value=180, required=False, allow_null=True)
    alt_m = serializers.FloatField(required=False, allow_null=True)
    vel_kmh = serializers.FloatField(required=False, allow_null=True)
    rumbo_deg = serializers.FloatField(min_value=0, max_value=360, required=False, allow_null=True)
    sats = serializers.IntegerField(required=False, allow_null=True)
    hdop = serializers.FloatField(required=False, allow_null=True)
    edad_ms = serializers.IntegerField(required=False, allow_null=True)
    modo = serializers.CharField(max_length=15, required=False, allow_blank=True)
    motivo = serializers.CharField(max_length=20, required=False, allow_blank=True)

    def validate(self, datos):
        if datos.get("fix") and (datos.get("lat") is None or datos.get("lon") is None):
            raise serializers.ValidationError("con fix=true son obligatorias lat y lon")
        return datos


class BateriaSerializer(serializers.Serializer):
    v = serializers.FloatField(required=False, allow_null=True)
    pct = serializers.IntegerField(required=False, allow_null=True)
