"""Configuración centralizada del sistema (Programación Orientada a Objetos).

``settings.py``, los servicios y ``main.py`` importan ÚNICAMENTE desde aquí.
Ningún módulo debe leer ``os.environ`` directamente fuera de este archivo.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
import os

BASE_DIR = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

logger = logging.getLogger("config")


def _env(clave: str, defecto: str = "") -> str:
    valor = os.environ.get(clave)
    return valor if valor is not None else defecto


def _env_bool(clave: str, defecto: bool) -> bool:
    valor = os.environ.get(clave)
    if valor is None or valor == "":
        return defecto
    return valor.strip().lower() in ("1", "true", "si", "sí", "yes", "on")


def _env_int(clave: str, defecto: int) -> int:
    valor = os.environ.get(clave)
    if valor is None or valor == "":
        return defecto
    try:
        return int(valor)
    except ValueError:
        logger.warning("Valor inválido para %s=%r, se usa el valor por defecto %s", clave, valor, defecto)
        return defecto


def _env_float(clave: str, defecto: float) -> float:
    valor = os.environ.get(clave)
    if valor is None or valor == "":
        return defecto
    try:
        return float(valor)
    except ValueError:
        logger.warning("Valor inválido para %s=%r, se usa el valor por defecto %s", clave, valor, defecto)
        return defecto


def _enmascarar(valor: str) -> str:
    if not valor:
        return "(vacío)"
    if len(valor) <= 8:
        return "****"
    return f"{valor[:4]}…{valor[-4:]}"


@dataclass(frozen=True)
class ServidorConfig:
    host: str
    puerto: int
    debug: bool
    secret_key: str
    allowed_hosts: list
    url_publica: str
    zona_horaria: str


@dataclass(frozen=True)
class BaseDatosConfig:
    ruta_sqlite: Path
    media_root: Path


@dataclass(frozen=True)
class Esp32Config:
    api_key: str
    ruta_telemetria: str
    ruta_eventos: str
    timeout_offline_s: int
    intervalo_estado_s: int


@dataclass(frozen=True)
class Esp32CamConfig:
    urls_por_dispositivo: dict
    urls_stream_por_dispositivo: dict
    api_key: str
    timeout_s: float
    usar_flash_noche: bool
    simulada: bool
    intervalo_status_s: int
    fps_max: int


@dataclass(frozen=True)
class MqttConfig:
    """Broker Mosquitto local (sección 2: contrato de tópicos ``ucsm/v1/<codigo>``)."""

    host: str
    puerto: int
    usuario: str
    clave: str
    prefijo: str
    keepalive: int
    client_id: str
    registrar_gps_csv: bool

    def topico(self, codigo: str, sufijo: str) -> str:
        return f"{self.prefijo}/{codigo}/{sufijo}"

    def comodin(self, sufijo: str) -> str:
        """Suscripción a un sufijo para TODOS los dispositivos: ``ucsm/v1/+/<sufijo>``."""
        return f"{self.prefijo}/+/{sufijo}"

    @staticmethod
    def codigo_de_topico(topico: str) -> str:
        """``ucsm/v1/NODO-01/cam/estado`` -> ``NODO-01`` (3.er segmento)."""
        partes = topico.split("/")
        return partes[2] if len(partes) >= 3 else ""

    @staticmethod
    def sufijo_de_topico(topico: str) -> str:
        """``ucsm/v1/NODO-01/cam/estado`` -> ``cam/estado``."""
        partes = topico.split("/")
        return "/".join(partes[3:]) if len(partes) >= 4 else ""


@dataclass(frozen=True)
class UmbralesDeteccionConfig:
    """Umbrales de las reglas definitivas (secciones 3.4 y 3.7).

    Se publican tal cual en ``ucsm/v1/<codigo>/config`` (retenido) para que el
    nodo use siempre los valores vigentes del servidor.
    """

    choque_g: float
    choque_db: float
    choque_ventana_ms: int
    golpe_baja_db: float
    estallido_alta_db: float
    planitud_min: float
    estallido_ventana_ms: int
    estallido_min_ms: int
    fusion_ms: int
    vibracion_g: float
    golpe_sin_estallido_ms: int
    quieto_g: float
    quieto_s: int
    marcha_s: int

    def a_dict(self) -> dict:
        return {
            "choque_g": self.choque_g,
            "choque_db": self.choque_db,
            "choque_ventana_ms": self.choque_ventana_ms,
            "golpe_baja_db": self.golpe_baja_db,
            "estallido_alta_db": self.estallido_alta_db,
            "planitud_min": self.planitud_min,
            "estallido_ventana_ms": self.estallido_ventana_ms,
            "estallido_min_ms": self.estallido_min_ms,
            "fusion_ms": self.fusion_ms,
            "vibracion_g": self.vibracion_g,
            "golpe_sin_estallido_ms": self.golpe_sin_estallido_ms,
            "quieto_g": self.quieto_g,
            "quieto_s": self.quieto_s,
            "marcha_s": self.marcha_s,
        }


@dataclass(frozen=True)
class ClaseVigilada:
    """Una clase del modelo YOLO y qué hace el servidor cuando la ve.

    Es el puente entre el nombre que devuelve el modelo (``model.names``) y el
    catálogo ``tipo_evento`` de la base de datos: sin esta correspondencia una
    detección no tiene ``TipoEvento``, y sin ``TipoEvento`` ``ReglasAccion`` no
    puede decidir nada y la incidencia nunca llega a Telegram.

    ``nivel_riesgo`` solo se usa al **sembrar** el catálogo; a partir de ahí
    manda la base de datos (editable desde la página "Tipos de evento"), igual
    que con los eventos del firmware.
    """

    clase: str                      # nombre exacto en model.names
    tipo_evento: str                # codigo de TipoEvento (máx. 20 caracteres)
    nombre: str                     # nombre legible para el catálogo
    nivel_riesgo: str               # ALTO / MEDIO / BAJO para la siembra inicial
    confianza_alerta: float         # umbral propio de esta clase
    solo_estacionado: bool          # True = solo alerta si está ESTACIONADO y armado
    descripcion: str = ""


# Catálogo de clases del modelo de la tesis (modelos_ia/yolo26_ucsm_best.pt).
# Los umbrales y la restricción de modo se pueden ajustar desde .env con
# YOLO_CONFIANZA_POR_CLASE y YOLO_CLASES_SOLO_ESTACIONADO.
CATALOGO_CLASES_YOLO = (
    ClaseVigilada(
        clase="arma_fuego", tipo_evento="ARMA_FUEGO", nombre="Arma de fuego detectada (YOLO)",
        nivel_riesgo="ALTO", confianza_alerta=0.50, solo_estacionado=False,
        descripcion="YOLO detectó un arma de fuego en la escena del vehículo",
    ),
    ClaseVigilada(
        clase="arma_blanca", tipo_evento="ARMA_BLANCA", nombre="Arma blanca detectada (YOLO)",
        nivel_riesgo="ALTO", confianza_alerta=0.50, solo_estacionado=False,
        descripcion="YOLO detectó un arma blanca (cuchillo, desarmador) en la escena",
    ),
    ClaseVigilada(
        clase="abriendo_puerta", tipo_evento="APERTURA_PUERTA", nombre="Apertura de puerta (YOLO)",
        nivel_riesgo="ALTO", confianza_alerta=0.55, solo_estacionado=False,
        descripcion="YOLO detectó a alguien manipulando/abriendo una puerta del vehículo",
    ),
    ClaseVigilada(
        clase="car broken", tipo_evento="VEHICULO_DANADO", nombre="Vehículo dañado (YOLO)",
        nivel_riesgo="ALTO", confianza_alerta=0.55, solo_estacionado=False,
        descripcion="YOLO detectó daño visible en el vehículo (vidrio roto, abolladura)",
    ),
    ClaseVigilada(
        clase="suspicious", tipo_evento="PERSONA_SOSPECHOSA", nombre="Persona sospechosa (YOLO)",
        nivel_riesgo="ALTO", confianza_alerta=0.60, solo_estacionado=False,
        descripcion="YOLO detectó una actitud sospechosa junto al vehículo",
    ),
    # 'person' es la clase del modelo genérico (yolo26n.pt) y mantiene la regla
    # de la sección 5.4: solo alerta con el vehículo ESTACIONADO y armado.
    ClaseVigilada(
        clase="person", tipo_evento="PERSONA", nombre="Persona detectada (YOLO)",
        nivel_riesgo="ALTO", confianza_alerta=0.60, solo_estacionado=True,
        descripcion="YOLO detectó una persona junto al vehículo",
    ),
)


@dataclass(frozen=True)
class YoloConfig:
    ruta_modelo: Path
    nombre_modelo: str
    confianza_minima: float
    confianza_alerta: float
    clases_objetivo: tuple
    tamano_imagen: int
    dispositivo: str
    clases_vigiladas: tuple = field(default_factory=tuple)

    def vigilada(self, clase: str) -> Optional[ClaseVigilada]:
        """La configuración de una clase del modelo, o ``None`` si no se vigila."""
        for vigilada in self.clases_vigiladas:
            if vigilada.clase == clase:
                return vigilada
        return None

    def umbral_alerta(self, clase: str) -> float:
        vigilada = self.vigilada(clase)
        return vigilada.confianza_alerta if vigilada else self.confianza_alerta

    def tipo_evento_de(self, clase: str) -> Optional[str]:
        vigilada = self.vigilada(clase)
        return vigilada.tipo_evento if vigilada else None


@dataclass(frozen=True)
class VisionContinuaConfig:
    activa: bool
    intervalo_ms: int
    frames_confirmacion: int
    cooldown_s: int


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    api_base: str
    timeout_s: float
    reintentos: int
    enviar_sin_persona: bool
    habilitado: bool
    comandos_activos: bool
    polling_timeout_s: int
    foto_timeout_s: float
    admin_chat_ids: tuple
    comandos_por_minuto: int
    invitacion_horas: int
    max_accesos_por_vehiculo: int


@dataclass(frozen=True)
class GoogleMapsConfig:
    api_key: str
    geocodificar: bool
    zoom: int


@dataclass(frozen=True)
class UmbralesConfig:
    impacto_g: float
    ruido_db: float
    inclinacion_deg: float
    giro_dps: float
    movimiento_kmh: float
    cooldown_s: int

    def a_dict(self) -> dict:
        return {
            "impacto_g": self.impacto_g,
            "ruido_db": self.ruido_db,
            "inclinacion_deg": self.inclinacion_deg,
            "giro_dps": self.giro_dps,
            "movimiento_kmh": self.movimiento_kmh,
            "cooldown_s": self.cooldown_s,
        }


@dataclass(frozen=True)
class ProcesamientoConfig:
    hilos_eventos: int
    tamano_cola: int


@dataclass(frozen=True)
class StreamCamaraConfig:
    """``ReceptorStreamCamara`` (sección 5.5): buffer circular del MJPEG."""

    segundos_buffer: float
    fps_navegador: int
    timeout_s: float
    reconexion_s: float


def _parsear_urls_camara(crudo: str) -> dict:
    urls: dict[str, str] = {}
    for par in crudo.split(","):
        par = par.strip()
        if not par or "=" not in par:
            continue
        codigo, url = par.split("=", 1)
        urls[codigo.strip()] = url.strip().rstrip("/")
    return urls


def _parsear_pares(crudo: str) -> dict:
    """``"clase=0.5,otra clase=0.6"`` -> ``{"clase": "0.5", "otra clase": "0.6"}``."""
    pares: dict[str, str] = {}
    for par in crudo.split(","):
        par = par.strip()
        if not par or "=" not in par:
            continue
        clave, valor = par.split("=", 1)
        pares[clave.strip()] = valor.strip()
    return pares


def _construir_clases_vigiladas(confianza_alerta_global: float, umbrales_raw: str,
                                solo_estacionado_raw: str) -> tuple:
    """Aplica al catálogo de clases los ajustes de ``.env``.

    * ``YOLO_CONFIANZA_POR_CLASE=arma_fuego=0.45,suspicious=0.7`` cambia el
      umbral de alerta de una clase concreta (lo demás queda igual).
    * ``YOLO_CLASES_SOLO_ESTACIONADO=person,suspicious`` lista las clases que
      solo alertan con el vehículo ESTACIONADO y armado; las que no aparecen
      alertan siempre (un arma es una emergencia también en marcha).
    """
    umbrales = _parsear_pares(umbrales_raw)
    restringidas = {c.strip() for c in solo_estacionado_raw.split(",") if c.strip()}

    conocidas = {v.clase for v in CATALOGO_CLASES_YOLO}
    for clase in list(umbrales) + list(restringidas):
        if clase not in conocidas:
            logger.warning(
                "La clase YOLO %r no está en CATALOGO_CLASES_YOLO (config.py); se ignora.", clase,
            )

    resultado = []
    for vigilada in CATALOGO_CLASES_YOLO:
        umbral = vigilada.confianza_alerta
        if vigilada.clase in umbrales:
            try:
                umbral = float(umbrales[vigilada.clase])
            except ValueError:
                logger.warning(
                    "Umbral inválido en YOLO_CONFIANZA_POR_CLASE para %r: %r; se usa %s.",
                    vigilada.clase, umbrales[vigilada.clase], umbral,
                )
        elif vigilada.clase == "person":
            # 'person' sigue atada al umbral global por compatibilidad (v2.0.0).
            umbral = confianza_alerta_global
        resultado.append(
            replace(vigilada, confianza_alerta=umbral, solo_estacionado=vigilada.clase in restringidas)
        )
    return tuple(resultado)


@dataclass
class Config:
    """Instancia única de configuración (patrón singleton por módulo)."""

    BASE_DIR: Path
    servidor: ServidorConfig
    bd: BaseDatosConfig
    esp32: Esp32Config
    esp32cam: Esp32CamConfig
    mqtt: MqttConfig
    yolo: YoloConfig
    vision: VisionContinuaConfig
    telegram: TelegramConfig
    maps: GoogleMapsConfig
    umbrales: UmbralesConfig
    deteccion: UmbralesDeteccionConfig
    procesamiento: ProcesamientoConfig
    stream: StreamCamaraConfig

    @classmethod
    def cargar(cls) -> "Config":
        secret_key = _env("DJANGO_SECRET_KEY")
        if not secret_key:
            secret_key = secrets.token_urlsafe(50)
            logger.warning(
                "DJANGO_SECRET_KEY no está definida en .env; se generó una clave temporal "
                "para esta ejecución. Defínela en .env para producción."
            )

        allowed_hosts_raw = _env("DJANGO_ALLOWED_HOSTS", "*")
        allowed_hosts = [h.strip() for h in allowed_hosts_raw.split(",") if h.strip()]

        ruta_sqlite_raw = _env("SQLITE_RUTA")
        ruta_sqlite = Path(ruta_sqlite_raw) if ruta_sqlite_raw else BASE_DIR / "db.sqlite3"

        media_root_raw = _env("MEDIA_ROOT")
        media_root = Path(media_root_raw) if media_root_raw else BASE_DIR / "media"

        ruta_modelo_raw = _env("YOLO_MODELO")
        ruta_modelo = Path(ruta_modelo_raw) if ruta_modelo_raw else BASE_DIR / "modelos_ia" / "yolo26n.pt"

        clases_raw = _env("YOLO_CLASES", "person")
        clases_objetivo = tuple(c.strip() for c in clases_raw.split(",") if c.strip())
        clases_vigiladas = _construir_clases_vigiladas(
            confianza_alerta_global=_env_float("YOLO_CONFIANZA_ALERTA", 0.60),
            umbrales_raw=_env("YOLO_CONFIANZA_POR_CLASE", ""),
            solo_estacionado_raw=_env("YOLO_CLASES_SOLO_ESTACIONADO", "person"),
        )

        servidor = ServidorConfig(
            host=_env("SERVIDOR_HOST", "0.0.0.0"),
            puerto=_env_int("SERVIDOR_PUERTO", 8000),
            debug=_env_bool("DJANGO_DEBUG", True),
            secret_key=secret_key,
            allowed_hosts=allowed_hosts,
            url_publica=_env("URL_PUBLICA", ""),
            zona_horaria=_env("ZONA_HORARIA", "America/Lima"),
        )

        bd = BaseDatosConfig(ruta_sqlite=ruta_sqlite, media_root=media_root)

        esp32 = Esp32Config(
            api_key=_env("ESP32_API_KEY", "tesis-ucsm-2026"),
            ruta_telemetria="/api/v1/telemetria/",
            ruta_eventos="/api/v1/eventos/",
            timeout_offline_s=_env_int("DISPOSITIVO_TIMEOUT_S", 10),
            intervalo_estado_s=_env_int("ESTADO_INTERVALO_S", 30),
        )

        esp32cam = Esp32CamConfig(
            urls_por_dispositivo=_parsear_urls_camara(_env("ESP32CAM_URLS", "")),
            urls_stream_por_dispositivo=_parsear_urls_camara(_env("ESP32CAM_STREAM_URLS", "")),
            api_key=_env("ESP32CAM_API_KEY", "tesis-ucsm-2026"),
            timeout_s=_env_float("ESP32CAM_TIMEOUT_S", 4.0),
            usar_flash_noche=_env_bool("ESP32CAM_FLASH", False),
            simulada=_env_bool("ESP32CAM_SIMULADA", False),
            intervalo_status_s=_env_int("ESP32CAM_STATUS_S", 30),
            fps_max=_env_int("ESP32CAM_FPS_MAX", 8),
        )

        mqtt = MqttConfig(
            host=_env("MQTT_HOST", "127.0.0.1"),
            puerto=_env_int("MQTT_PUERTO", 1883),
            usuario=_env("MQTT_USUARIO", "servidor"),
            clave=_env("MQTT_CLAVE", ""),
            prefijo=_env("MQTT_PREFIJO", "ucsm/v1").strip("/"),
            keepalive=_env_int("MQTT_KEEPALIVE", 15),
            client_id=_env("MQTT_CLIENT_ID", "servidor-ucsm"),
            registrar_gps_csv=_env_bool("MQTT_GPS_CSV", False),
        )

        yolo = YoloConfig(
            ruta_modelo=ruta_modelo,
            nombre_modelo=_env("YOLO_NOMBRE", "yolo26n"),
            confianza_minima=_env_float("YOLO_CONFIANZA", 0.50),
            confianza_alerta=_env_float("YOLO_CONFIANZA_ALERTA", 0.60),
            clases_objetivo=clases_objetivo,
            tamano_imagen=_env_int("YOLO_IMGSZ", 640),
            dispositivo=_env("YOLO_DEVICE", "cpu"),
            clases_vigiladas=clases_vigiladas,
        )

        vision = VisionContinuaConfig(
            activa=_env_bool("VISION_CONTINUA", True),
            intervalo_ms=_env_int("VISION_INTERVALO_MS", 500),
            frames_confirmacion=_env_int("VISION_FRAMES_CONFIRMACION", 2),
            cooldown_s=_env_int("VISION_COOLDOWN_S", 30),
        )

        admin_chat_ids_raw = _env("TELEGRAM_ADMIN_CHAT_IDS", "")
        admin_chat_ids = tuple(c.strip() for c in admin_chat_ids_raw.split(",") if c.strip())

        telegram = TelegramConfig(
            bot_token=_env("TELEGRAM_BOT_TOKEN", ""),
            api_base="https://api.telegram.org",
            timeout_s=_env_float("TELEGRAM_TIMEOUT_S", 10.0),
            reintentos=_env_int("TELEGRAM_REINTENTOS", 3),
            enviar_sin_persona=_env_bool("TELEGRAM_ALERTAR_SIN_PERSONA", True),
            habilitado=_env_bool("TELEGRAM_HABILITADO", True),
            comandos_activos=_env_bool("TELEGRAM_BOT_COMANDOS", True),
            polling_timeout_s=_env_int("TELEGRAM_POLLING_TIMEOUT_S", 25),
            foto_timeout_s=_env_float("TELEGRAM_FOTO_TIMEOUT_S", 6.0),
            admin_chat_ids=admin_chat_ids,
            comandos_por_minuto=_env_int("TELEGRAM_COMANDOS_POR_MINUTO", 20),
            invitacion_horas=_env_int("TELEGRAM_INVITACION_HORAS", 24),
            max_accesos_por_vehiculo=_env_int("TELEGRAM_MAX_ACCESOS_POR_VEHICULO", 6),
        )

        maps = GoogleMapsConfig(
            api_key=_env("GOOGLE_MAPS_API_KEY", ""),
            geocodificar=_env_bool("GOOGLE_GEOCODING", False),
            zoom=_env_int("MAPA_ZOOM", 16),
        )

        umbrales = UmbralesConfig(
            impacto_g=_env_float("UMBRAL_IMPACTO_G", 0.35),
            ruido_db=_env_float("UMBRAL_RUIDO_DB", 85),
            inclinacion_deg=_env_float("UMBRAL_INCLINACION_DEG", 3),
            giro_dps=_env_float("UMBRAL_GIRO_DPS", 60),
            movimiento_kmh=_env_float("UMBRAL_MOVIMIENTO_KMH", 8),
            cooldown_s=_env_int("UMBRAL_COOLDOWN_S", 15),
        )

        deteccion = UmbralesDeteccionConfig(
            choque_g=_env_float("DET_CHOQUE_G", 4.0),
            choque_db=_env_float("DET_CHOQUE_DB", 112.0),
            choque_ventana_ms=_env_int("DET_CHOQUE_VENTANA_MS", 500),
            golpe_baja_db=_env_float("DET_GOLPE_BAJA_DB", 15.0),
            estallido_alta_db=_env_float("DET_ESTALLIDO_ALTA_DB", 12.0),
            planitud_min=_env_float("DET_PLANITUD_MIN", 0.3),
            estallido_ventana_ms=_env_int("DET_ESTALLIDO_VENTANA_MS", 300),
            estallido_min_ms=_env_int("DET_ESTALLIDO_MIN_MS", 150),
            fusion_ms=_env_int("DET_FUSION_MS", 500),
            vibracion_g=_env_float("DET_VIBRACION_G", 0.15),
            golpe_sin_estallido_ms=_env_int("DET_GOLPE_SIN_ESTALLIDO_MS", 1000),
            quieto_g=_env_float("DET_QUIETO_G", 0.05),
            quieto_s=_env_int("DET_QUIETO_S", 300),
            marcha_s=_env_int("DET_MARCHA_S", 10),
        )

        procesamiento = ProcesamientoConfig(
            hilos_eventos=_env_int("EVENTOS_HILOS", 1),
            tamano_cola=_env_int("EVENTOS_COLA", 50),
        )

        stream = StreamCamaraConfig(
            segundos_buffer=_env_float("STREAM_SEGUNDOS_BUFFER", 3.0),
            fps_navegador=_env_int("STREAM_FPS_NAVEGADOR", 5),
            timeout_s=_env_float("STREAM_TIMEOUT_S", 8.0),
            reconexion_s=_env_float("STREAM_RECONEXION_S", 3.0),
        )

        return cls(
            BASE_DIR=BASE_DIR,
            servidor=servidor,
            bd=bd,
            esp32=esp32,
            esp32cam=esp32cam,
            mqtt=mqtt,
            yolo=yolo,
            vision=vision,
            telegram=telegram,
            maps=maps,
            umbrales=umbrales,
            deteccion=deteccion,
            procesamiento=procesamiento,
            stream=stream,
        )

    def url_camara(self, codigo_dispositivo: str) -> Optional[str]:
        return self.esp32cam.urls_por_dispositivo.get(codigo_dispositivo)

    def url_stream_camara(self, codigo_dispositivo: str) -> Optional[str]:
        """URL del MJPEG continuo (puerto 81) que lee ``ReceptorStreamCamara``."""
        return self.esp32cam.urls_stream_por_dispositivo.get(codigo_dispositivo)

    def resumen_seguro(self) -> dict:
        """Devuelve la configuración agrupada con los secretos enmascarados."""
        return {
            "Servidor": {
                "Host": self.servidor.host,
                "Puerto": self.servidor.puerto,
                "Debug": self.servidor.debug,
                "Clave secreta Django": _enmascarar(self.servidor.secret_key),
                "Hosts permitidos": ", ".join(self.servidor.allowed_hosts),
                "URL pública": self.servidor.url_publica or "(no configurada)",
                "Zona horaria": self.servidor.zona_horaria,
            },
            "Nodo ESP32-S3": {
                "API key": _enmascarar(self.esp32.api_key),
                "Timeout fuera de línea (s)": self.esp32.timeout_offline_s,
                "Intervalo de estado (s)": self.esp32.intervalo_estado_s,
            },
            "Broker MQTT (Mosquitto)": {
                "Host": f"{self.mqtt.host}:{self.mqtt.puerto}",
                "Usuario": self.mqtt.usuario or "(vacío)",
                "Contraseña": _enmascarar(self.mqtt.clave),
                "Prefijo de tópicos": f"{self.mqtt.prefijo}/<codigo>/…",
                "Keepalive (s)": self.mqtt.keepalive,
                "Client ID": self.mqtt.client_id,
            },
            "Cámara ESP32-CAM": {
                "Dispositivos configurados": ", ".join(self.esp32cam.urls_por_dispositivo) or "(ninguno)",
                "Stream MJPEG (puerto 81)": ", ".join(
                    f"{codigo}={url}" for codigo, url in self.esp32cam.urls_stream_por_dispositivo.items()
                ) or "(ninguno)",
                "API key": _enmascarar(self.esp32cam.api_key),
                "Timeout (s)": self.esp32cam.timeout_s,
                "FPS máximo": self.esp32cam.fps_max,
                "Simulada": self.esp32cam.simulada,
            },
            "YOLO": {
                "Modelo": self.yolo.nombre_modelo,
                "Ruta del modelo": str(self.yolo.ruta_modelo),
                "Confianza mínima": self.yolo.confianza_minima,
                "Confianza de alerta": self.yolo.confianza_alerta,
                "Dispositivo de inferencia": self.yolo.dispositivo,
                "Clases que pide al modelo": ", ".join(self.yolo.clases_objetivo) or "(todas)",
            },
            "Clases que generan incidencia": {
                f"{v.clase} → {v.tipo_evento}": (
                    f"confianza ≥ {v.confianza_alerta:.0%} · "
                    + ("solo ESTACIONADO y armado" if v.solo_estacionado else "alerta en cualquier modo")
                )
                for v in self.yolo.clases_vigiladas
            },
            "Visión continua": {
                "Activa": self.vision.activa,
                "Intervalo (ms)": self.vision.intervalo_ms,
                "Frames de confirmación": self.vision.frames_confirmacion,
                "Cooldown (s)": self.vision.cooldown_s,
            },
            "Telegram": {
                "Habilitado": self.telegram.habilitado,
                "Token del bot": _enmascarar(self.telegram.bot_token),
                "Alertar sin persona": self.telegram.enviar_sin_persona,
                "Comandos del bot activos": self.telegram.comandos_activos,
                "Timeout de long polling (s)": self.telegram.polling_timeout_s,
                "Chats administradores": ", ".join(self.telegram.admin_chat_ids) or "(ninguno)",
                "Comandos por minuto (límite)": self.telegram.comandos_por_minuto,
                "Vigencia de invitaciones (h)": self.telegram.invitacion_horas,
                "Máx. accesos por vehículo": self.telegram.max_accesos_por_vehiculo,
            },
            "Google Maps": {
                "API key": _enmascarar(self.maps.api_key),
                "Geocodificación inversa": self.maps.geocodificar,
            },
            "Umbrales legado (API HTTP de respaldo)": self.umbrales.a_dict(),
            "Umbrales de detección (publicados en MQTT 'config')": self.deteccion.a_dict(),
        }


config = Config.cargar()
