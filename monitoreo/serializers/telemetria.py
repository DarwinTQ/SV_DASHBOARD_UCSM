"""Valida el JSON de ``POST /api/v1/telemetria/`` (contrato fijo, sección 7.2)."""
from rest_framework import serializers

from .comunes import BateriaSerializer, GpsSerializer, SistemaSerializer, WifiSerializer


class ImuTelemetriaSerializer(serializers.Serializer):
    ax = serializers.FloatField(required=False, allow_null=True)
    ay = serializers.FloatField(required=False, allow_null=True)
    az = serializers.FloatField(required=False, allow_null=True)
    a_max_g = serializers.FloatField(required=False, allow_null=True)
    vib_rms_g = serializers.FloatField(required=False, allow_null=True)
    vib_pp_g = serializers.FloatField(required=False, allow_null=True)
    giro_max_dps = serializers.FloatField(required=False, allow_null=True)
    pitch_deg = serializers.FloatField(required=False, allow_null=True)
    roll_deg = serializers.FloatField(required=False, allow_null=True)
    muestras = serializers.IntegerField(required=False, allow_null=True)


class AudioTelemetriaSerializer(serializers.Serializer):
    rms_dbfs = serializers.FloatField(required=False, allow_null=True)
    spl_db = serializers.FloatField(required=False, allow_null=True)
    pico_dbfs = serializers.FloatField(required=False, allow_null=True)
    pico_spl_db = serializers.FloatField(required=False, allow_null=True)
    muestras = serializers.IntegerField(required=False, allow_null=True)


class HttpTelemetriaSerializer(serializers.Serializer):
    rtt_prev_ms = serializers.IntegerField(required=False, allow_null=True)
    ok = serializers.IntegerField(required=False, allow_null=True)
    fallos = serializers.IntegerField(required=False, allow_null=True)
    descartados = serializers.IntegerField(required=False, allow_null=True)


class TelemetriaSerializer(serializers.Serializer):
    codigo = serializers.CharField(max_length=20)
    firmware = serializers.CharField(max_length=10, required=False, allow_blank=True)
    mac = serializers.CharField(max_length=17, required=False, allow_blank=True)
    ip = serializers.CharField(max_length=45, required=False, allow_blank=True)
    boot_id = serializers.IntegerField(required=False, allow_null=True)
    seq = serializers.IntegerField(required=False, allow_null=True)
    uptime_ms = serializers.IntegerField(required=False, allow_null=True)
    ts_ms = serializers.IntegerField(required=False, allow_null=True)
    ts_fuente = serializers.ChoiceField(choices=["SERVIDOR", "NTP", "NINGUNA"], required=False, default="NINGUNA")
    armado = serializers.BooleanField(required=False, default=False)

    wifi = WifiSerializer(required=False, allow_null=True)
    sistema = SistemaSerializer(required=False, allow_null=True)
    gps = GpsSerializer(required=False, allow_null=True)
    imu = ImuTelemetriaSerializer(required=False, allow_null=True)
    audio = AudioTelemetriaSerializer(required=False, allow_null=True)
    bateria = BateriaSerializer(required=False, allow_null=True)
    http = HttpTelemetriaSerializer(required=False, allow_null=True)
