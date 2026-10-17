"""Valida el payload de un evento del nodo.

Lo usan los DOS caminos de entrada, con el mismo contrato:
  * ``PuenteMQTT`` para ``ucsm/v1/<codigo>/evento`` (QoS 1) — camino normal;
  * ``POST /api/v1/eventos/`` (sección 7.3) — respaldo que se mantiene.

Los campos de la v2.0.0 (``valor``/``umbral``/``unidad``) siguen aceptándose tal
cual; los de la v3.0.0 (``nivel``, ``evidencia``, ``modo``, ``armado``) se
agregan como **opcionales**, de modo que un nodo viejo y un nodo nuevo validan
contra el mismo serializador sin romper nada.
"""
from rest_framework import serializers

from .comunes import BateriaSerializer, GpsSerializer, WifiSerializer

# Reglas definitivas (sección 3.4 del contrato MQTT).
TIPOS_EVENTO_REGLAS = (
    "CHOQUE", "IMPACTO_SIN_CONFIRMAR",
    "ROTURA_VENTANA", "VIDRIO_ROTO_EXTERNO", "GOLPE_MANIPULACION",
)
# Reglas legado de la v2.0.0 (cfg::REGLAS_LEGADO; siguen en el catálogo).
TIPOS_EVENTO_LEGADO = ("VIBRACION", "RUIDO", "MANIPULACION", "MOVIMIENTO")

TIPOS_EVENTO_SENSOR = TIPOS_EVENTO_REGLAS + TIPOS_EVENTO_LEGADO

NIVELES_EVENTO = ("ALERTA", "ALERTA_LEVE", "REGISTRO")
MODOS_VEHICULO = ("EN_MARCHA", "ESTACIONADO")


class ImuEventoSerializer(serializers.Serializer):
    pitch_deg = serializers.FloatField(required=False, allow_null=True)
    roll_deg = serializers.FloatField(required=False, allow_null=True)


class EvidenciaEventoSerializer(serializers.Serializer):
    """Evidencia numérica de la regla que disparó el evento (sección 3.4).

    Para CHOQUE / IMPACTO_SIN_CONFIRMAR llegan ``a_pico_g``, ``spl_pico_db`` y
    ``delta_ms``; para la regla de rotura, las energías por banda, el piso de
    ruido, la planitud, la duración del estallido y la vibración fusionada.
    Todos son opcionales porque dependen de la regla.
    """

    # CHOQUE / IMPACTO_SIN_CONFIRMAR
    a_pico_g = serializers.FloatField(required=False, allow_null=True)
    spl_pico_db = serializers.FloatField(required=False, allow_null=True)
    delta_ms = serializers.IntegerField(required=False, allow_null=True)
    # ROTURA_VENTANA / VIDRIO_ROTO_EXTERNO / GOLPE_MANIPULACION
    e_baja_db = serializers.FloatField(required=False, allow_null=True)
    piso_baja_db = serializers.FloatField(required=False, allow_null=True)
    e_alta_db = serializers.FloatField(required=False, allow_null=True)
    piso_alta_db = serializers.FloatField(required=False, allow_null=True)
    planitud = serializers.FloatField(required=False, allow_null=True)
    duracion_ms = serializers.IntegerField(required=False, allow_null=True)
    vib_g = serializers.FloatField(required=False, allow_null=True)
    delta_vib_ms = serializers.IntegerField(required=False, allow_null=True)


class EventoSerializer(serializers.Serializer):
    codigo = serializers.CharField(max_length=20)
    evento_uid = serializers.CharField(max_length=60)
    tipo = serializers.ChoiceField(choices=TIPOS_EVENTO_SENSOR)
    ts_ms = serializers.IntegerField(required=False, allow_null=True)
    ts_fuente = serializers.ChoiceField(choices=["SERVIDOR", "NTP", "NINGUNA"], required=False, default="NINGUNA")

    # --- v3.0.0 (reglas definitivas) ---
    nivel = serializers.ChoiceField(choices=NIVELES_EVENTO, required=False, allow_null=True)
    modo = serializers.ChoiceField(choices=MODOS_VEHICULO, required=False, allow_null=True)
    armado = serializers.BooleanField(required=False, default=True)
    evidencia = EvidenciaEventoSerializer(required=False, allow_null=True)

    # --- v2.0.0 (reglas legado) ---
    valor = serializers.FloatField(required=False, allow_null=True)
    umbral = serializers.FloatField(required=False, allow_null=True)
    unidad = serializers.CharField(max_length=10, required=False, allow_blank=True)

    aceleracion_g = serializers.FloatField(required=False, allow_null=True)
    nivel_audio_db = serializers.FloatField(required=False, allow_null=True)

    gps = GpsSerializer(required=False, allow_null=True)
    imu = ImuEventoSerializer(required=False, allow_null=True)
    wifi = WifiSerializer(required=False, allow_null=True)
    bateria = BateriaSerializer(required=False, allow_null=True)
