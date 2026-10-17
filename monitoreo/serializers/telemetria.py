"""Valida la telemetría del nodo (contrato fijo, sección 7.2 / 3.6).

La usan los dos caminos: ``PuenteMQTT`` para ``ucsm/v1/<codigo>/telemetria``
(cada 1 s, camino normal) y ``POST /api/v1/telemetria/`` como respaldo.
"""
from rest_framework import serializers

from .comunes import BateriaSerializer, GpsSerializer, SistemaSerializer, WifiSerializer

MODOS_VEHICULO = ("EN_MARCHA", "ESTACIONADO")
FUENTES_MODO = ("INERCIAL", "CONTACTO", "FORZADO")


class ImuTelemetriaSerializer(serializers.Serializer):
    ax = serializers.FloatField(required=False, allow_null=True)
    ay = serializers.FloatField(required=False, allow_null=True)
    az = serializers.FloatField(required=False, allow_null=True)
    # a_max_g llega hasta 16 g desde la v3.0.0 (el MPU pasó de ±4 g a ±16 g).
    a_max_g = serializers.FloatField(required=False, allow_null=True)
    vib_max_g = serializers.FloatField(required=False, allow_null=True)
    variacion_g = serializers.FloatField(required=False, allow_null=True)
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
    # v3.0.0: pico de dB SPL del segundo, piso de ruido aprendido por banda y
    # tiempo de CPU de la FFT (debe quedar muy por debajo de los 16 000 µs del
    # bloque; es una de las evidencias del capítulo de resultados).
    spl_pico_db = serializers.FloatField(required=False, allow_null=True)
    piso_baja_db = serializers.FloatField(required=False, allow_null=True)
    piso_alta_db = serializers.FloatField(required=False, allow_null=True)
    fft_us = serializers.IntegerField(required=False, allow_null=True)
    muestras = serializers.IntegerField(required=False, allow_null=True)


class HttpTelemetriaSerializer(serializers.Serializer):
    rtt_prev_ms = serializers.IntegerField(required=False, allow_null=True)
    ok = serializers.IntegerField(required=False, allow_null=True)
    fallos = serializers.IntegerField(required=False, allow_null=True)
    descartados = serializers.IntegerField(required=False, allow_null=True)


class MqttTelemetriaSerializer(serializers.Serializer):
    """Salud del enlace MQTT del nodo (reemplaza al bloque ``http`` de la v2)."""

    reconexiones = serializers.IntegerField(required=False, allow_null=True)
    publicados = serializers.IntegerField(required=False, allow_null=True)
    descartados = serializers.IntegerField(required=False, allow_null=True)
    rtt_sync_ms = serializers.IntegerField(required=False, allow_null=True)


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

    # --- v3.0.0: modo del vehículo (sección 3.6) ---
    modo = serializers.ChoiceField(choices=MODOS_VEHICULO, required=False, allow_null=True)
    modo_fuente = serializers.ChoiceField(choices=FUENTES_MODO, required=False, allow_null=True)
    contacto = serializers.BooleanField(required=False, allow_null=True)

    wifi = WifiSerializer(required=False, allow_null=True)
    sistema = SistemaSerializer(required=False, allow_null=True)
    gps = GpsSerializer(required=False, allow_null=True)
    imu = ImuTelemetriaSerializer(required=False, allow_null=True)
    audio = AudioTelemetriaSerializer(required=False, allow_null=True)
    bateria = BateriaSerializer(required=False, allow_null=True)
    http = HttpTelemetriaSerializer(required=False, allow_null=True)
    mqtt = MqttTelemetriaSerializer(required=False, allow_null=True)
