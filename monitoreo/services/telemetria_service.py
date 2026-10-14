"""Lógica de negocio de ``POST /api/v1/telemetria/`` (sección 7.2)."""
import logging

from django.utils import timezone

from config import config
from monitoreo.models import Dispositivo, EstadoDispositivo

from .estado_en_vivo import EstadoEnVivo
from .notificador_tiempo_real import NotificadorTiempoReal

logger = logging.getLogger("monitoreo.servicios")


class TelemetriaService:
    """Procesa cada lectura periódica del nodo ESP32-S3."""

    def __init__(self, estado_en_vivo: EstadoEnVivo | None = None):
        self._estado_en_vivo = estado_en_vivo or EstadoEnVivo.instancia()

    def procesar(self, dispositivo: Dispositivo, datos: dict) -> None:
        self._actualizar_dispositivo(dispositivo, datos)
        self._estado_en_vivo.actualizar_telemetria(dispositivo.codigo, datos)
        self._guardar_heartbeat_si_corresponde(dispositivo, datos)
        NotificadorTiempoReal.telemetria(dispositivo.codigo, self._resumen_para_panel(datos))

    def _actualizar_dispositivo(self, dispositivo: Dispositivo, datos: dict) -> None:
        campos = []
        ip = datos.get("ip")
        if ip:
            dispositivo.ip = ip
            campos.append("ip")
        mac = datos.get("mac")
        if mac:
            dispositivo.mac_esp32 = mac
            campos.append("mac_esp32")
        firmware = datos.get("firmware")
        if firmware:
            dispositivo.version_firmware = firmware
            campos.append("version_firmware")
        dispositivo.ultimo_contacto = timezone.now()
        campos.append("ultimo_contacto")
        dispositivo.save(update_fields=campos)

    def _guardar_heartbeat_si_corresponde(self, dispositivo: Dispositivo, datos: dict) -> None:
        intervalo_s = config.esp32.intervalo_estado_s
        ultimo = EstadoDispositivo.objects.filter(dispositivo=dispositivo).order_by("-fecha_hora").first()
        if ultimo and (timezone.now() - ultimo.fecha_hora).total_seconds() < intervalo_s:
            return

        wifi = datos.get("wifi") or {}
        sistema = datos.get("sistema") or {}
        bateria = datos.get("bateria") or {}
        EstadoDispositivo.objects.create(
            dispositivo=dispositivo,
            en_linea=True,
            rssi_wifi_dbm=wifi.get("rssi_dbm"),
            voltaje_v=bateria.get("v"),
            temperatura_c=sistema.get("temp_c"),
            ram_libre_kb=sistema.get("ram_libre_kb"),
        )

    @staticmethod
    def _resumen_para_panel(datos: dict) -> dict:
        return {
            "armado": datos.get("armado"),
            "wifi": datos.get("wifi"),
            "sistema": datos.get("sistema"),
            "gps": datos.get("gps"),
            "imu": datos.get("imu"),
            "audio": datos.get("audio"),
            "bateria": datos.get("bateria"),
        }
