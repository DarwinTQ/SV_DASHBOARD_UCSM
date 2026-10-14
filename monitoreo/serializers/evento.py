"""Valida el JSON de ``POST /api/v1/eventos/`` (contrato fijo, sección 7.3)."""
from rest_framework import serializers

from .comunes import BateriaSerializer, GpsSerializer, WifiSerializer

TIPOS_EVENTO_SENSOR = ("VIBRACION", "RUIDO", "MANIPULACION", "MOVIMIENTO")


class ImuEventoSerializer(serializers.Serializer):
    pitch_deg = serializers.FloatField(required=False, allow_null=True)
    roll_deg = serializers.FloatField(required=False, allow_null=True)


class EventoSerializer(serializers.Serializer):
    codigo = serializers.CharField(max_length=20)
    evento_uid = serializers.CharField(max_length=60)
    tipo = serializers.ChoiceField(choices=TIPOS_EVENTO_SENSOR)
    ts_ms = serializers.IntegerField(required=False, allow_null=True)
    ts_fuente = serializers.ChoiceField(choices=["SERVIDOR", "NTP", "NINGUNA"], required=False, default="NINGUNA")
    valor = serializers.FloatField(required=False, allow_null=True)
    umbral = serializers.FloatField(required=False, allow_null=True)
    unidad = serializers.CharField(max_length=10, required=False, allow_blank=True)
    aceleracion_g = serializers.FloatField(required=False, allow_null=True)
    nivel_audio_db = serializers.FloatField(required=False, allow_null=True)

    gps = GpsSerializer(required=False, allow_null=True)
    imu = ImuEventoSerializer(required=False, allow_null=True)
    wifi = WifiSerializer(required=False, allow_null=True)
    bateria = BateriaSerializer(required=False, allow_null=True)
