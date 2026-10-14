"""Configuración centralizada del sistema (Programación Orientada a Objetos).

``settings.py``, los servicios y ``main.py`` importan ÚNICAMENTE desde aquí.
Ningún módulo debe leer ``os.environ`` directamente fuera de este archivo.
"""
from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass, field
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
    api_key: str
    timeout_s: float
    usar_flash_noche: bool
    simulada: bool
    intervalo_status_s: int


@dataclass(frozen=True)
class YoloConfig:
    ruta_modelo: Path
    nombre_modelo: str
    confianza_minima: float
    confianza_alerta: float
    clases_objetivo: tuple
    tamano_imagen: int
    dispositivo: str


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


def _parsear_urls_camara(crudo: str) -> dict:
    urls: dict[str, str] = {}
    for par in crudo.split(","):
        par = par.strip()
        if not par or "=" not in par:
            continue
        codigo, url = par.split("=", 1)
        urls[codigo.strip()] = url.strip().rstrip("/")
    return urls


@dataclass
class Config:
    """Instancia única de configuración (patrón singleton por módulo)."""

    BASE_DIR: Path
    servidor: ServidorConfig
    bd: BaseDatosConfig
    esp32: Esp32Config
    esp32cam: Esp32CamConfig
    yolo: YoloConfig
    vision: VisionContinuaConfig
    telegram: TelegramConfig
    maps: GoogleMapsConfig
    umbrales: UmbralesConfig
    procesamiento: ProcesamientoConfig

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
            api_key=_env("ESP32CAM_API_KEY", "tesis-ucsm-2026"),
            timeout_s=_env_float("ESP32CAM_TIMEOUT_S", 4.0),
            usar_flash_noche=_env_bool("ESP32CAM_FLASH", False),
            simulada=_env_bool("ESP32CAM_SIMULADA", False),
            intervalo_status_s=_env_int("ESP32CAM_STATUS_S", 30),
        )

        yolo = YoloConfig(
            ruta_modelo=ruta_modelo,
            nombre_modelo=_env("YOLO_NOMBRE", "yolo26n"),
            confianza_minima=_env_float("YOLO_CONFIANZA", 0.50),
            confianza_alerta=_env_float("YOLO_CONFIANZA_ALERTA", 0.60),
            clases_objetivo=clases_objetivo,
            tamano_imagen=_env_int("YOLO_IMGSZ", 640),
            dispositivo=_env("YOLO_DEVICE", "cpu"),
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

        procesamiento = ProcesamientoConfig(
            hilos_eventos=_env_int("EVENTOS_HILOS", 1),
            tamano_cola=_env_int("EVENTOS_COLA", 50),
        )

        return cls(
            BASE_DIR=BASE_DIR,
            servidor=servidor,
            bd=bd,
            esp32=esp32,
            esp32cam=esp32cam,
            yolo=yolo,
            vision=vision,
            telegram=telegram,
            maps=maps,
            umbrales=umbrales,
            procesamiento=procesamiento,
        )

    def url_camara(self, codigo_dispositivo: str) -> Optional[str]:
        return self.esp32cam.urls_por_dispositivo.get(codigo_dispositivo)

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
            "Cámara ESP32-CAM": {
                "Dispositivos configurados": ", ".join(self.esp32cam.urls_por_dispositivo) or "(ninguno)",
                "API key": _enmascarar(self.esp32cam.api_key),
                "Timeout (s)": self.esp32cam.timeout_s,
                "Simulada": self.esp32cam.simulada,
            },
            "YOLO": {
                "Modelo": self.yolo.nombre_modelo,
                "Ruta del modelo": str(self.yolo.ruta_modelo),
                "Confianza mínima": self.yolo.confianza_minima,
                "Confianza de alerta": self.yolo.confianza_alerta,
                "Dispositivo de inferencia": self.yolo.dispositivo,
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
            "Umbrales enviados al ESP32": self.umbrales.a_dict(),
        }


config = Config.cargar()
