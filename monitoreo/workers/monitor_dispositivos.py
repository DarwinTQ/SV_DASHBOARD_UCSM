"""Supervisa conectividad del nodo y estado de la ESP32-CAM (sección 8.3)."""
from __future__ import annotations

import time

from django.utils import timezone

from config import config
from monitoreo.models import Dispositivo, EstadoDispositivo
from monitoreo.services.camara_service import FabricaCamara
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal

from .base_worker import WorkerBase

CICLO_S = 5


class MonitorDispositivos(WorkerBase):
    """Ciclo cada 5 s: detecta nodos fuera de línea y refresca el estado de la cámara."""

    nombre = "MonitorDispositivos"

    def __init__(self):
        super().__init__()
        self._estado_en_vivo = EstadoEnVivo.instancia()
        self._fuera_de_linea_notificado: set[str] = set()
        self._ultimo_status_camara: dict[str, float] = {}

    def ciclo(self) -> None:
        for dispositivo in Dispositivo.objects.all():
            try:
                self._verificar_conectividad(dispositivo)
                self._verificar_camara(dispositivo)
            except Exception:
                self.logger.exception("Error monitoreando %s", dispositivo.codigo)
        time.sleep(CICLO_S)

    @staticmethod
    def _en_linea(dispositivo: Dispositivo) -> bool:
        if dispositivo.ultimo_contacto is None:
            return False
        return (timezone.now() - dispositivo.ultimo_contacto).total_seconds() <= config.esp32.timeout_offline_s

    def _verificar_conectividad(self, dispositivo: Dispositivo) -> None:
        codigo = dispositivo.codigo
        en_linea = self._en_linea(dispositivo)

        if en_linea:
            if codigo in self._fuera_de_linea_notificado:
                self._fuera_de_linea_notificado.discard(codigo)
                NotificadorTiempoReal.dispositivo_estado({
                    "codigo": codigo, "en_linea": True,
                    "ultimo_contacto": dispositivo.ultimo_contacto.isoformat(),
                })
            return

        if codigo not in self._fuera_de_linea_notificado:
            self._fuera_de_linea_notificado.add(codigo)
            NotificadorTiempoReal.dispositivo_estado({
                "codigo": codigo, "en_linea": False,
                "ultimo_contacto": dispositivo.ultimo_contacto.isoformat() if dispositivo.ultimo_contacto else None,
            })

        ultimo = EstadoDispositivo.objects.filter(dispositivo=dispositivo).order_by("-fecha_hora").first()
        if not ultimo or (timezone.now() - ultimo.fecha_hora).total_seconds() >= config.esp32.intervalo_estado_s:
            EstadoDispositivo.objects.create(dispositivo=dispositivo, en_linea=False)

    def _verificar_camara(self, dispositivo: Dispositivo) -> None:
        codigo = dispositivo.codigo
        ahora = time.time()
        ultimo = self._ultimo_status_camara.get(codigo, 0)
        if ahora - ultimo < config.esp32cam.intervalo_status_s:
            return
        self._ultimo_status_camara[codigo] = ahora

        estado = FabricaCamara.para(codigo).obtener_estado()
        if estado is None:
            self._estado_en_vivo.actualizar_estado_camara(codigo, {"ok": False})
            return

        self._estado_en_vivo.actualizar_estado_camara(codigo, estado)
        mac = estado.get("mac")
        if mac and dispositivo.mac_esp32cam != mac:
            dispositivo.mac_esp32cam = mac
            dispositivo.save(update_fields=["mac_esp32cam"])
