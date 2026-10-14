"""Serializadores anidados reutilizados por telemetría y eventos."""
from rest_framework import serializers


class WifiSerializer(serializers.Serializer):
    rssi_dbm = serializers.IntegerField(min_value=-120, max_value=0, required=False, allow_null=True)


class SistemaSerializer(serializers.Serializer):
    ram_libre_kb = serializers.IntegerField(required=False, allow_null=True)
    temp_c = serializers.FloatField(required=False, allow_null=True)


class GpsSerializer(serializers.Serializer):
    fix = serializers.BooleanField()
    sats = serializers.IntegerField()
    lat = serializers.FloatField(min_value=-90, max_value=90, required=False, allow_null=True)
    lon = serializers.FloatField(min_value=-180, max_value=180, required=False, allow_null=True)
    vel_kmh = serializers.FloatField(required=False, allow_null=True)
    alt_m = serializers.FloatField(required=False, allow_null=True)
    hdop = serializers.FloatField(required=False, allow_null=True)


class BateriaSerializer(serializers.Serializer):
    v = serializers.FloatField(required=False, allow_null=True)
    pct = serializers.IntegerField(required=False, allow_null=True)
