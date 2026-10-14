from django.db import models

from .vehiculo import Vehiculo


class Dispositivo(models.Model):
    """Nodo ESP32-S3 (+ ESP32-CAM asociada) instalado en un vehículo."""

    ESTADOS = [("ACTIVO", "Activo"), ("INACTIVO", "Inactivo"), ("MANTENIMIENTO", "Mantenimiento")]

    vehiculo = models.OneToOneField(
        Vehiculo, on_delete=models.PROTECT,
        related_name="dispositivo", db_column="id_vehiculo",
    )
    codigo = models.CharField(max_length=20, unique=True, help_text="Ej.: NODO-01")
    mac_esp32 = models.CharField("MAC ESP32", max_length=17, blank=True)
    mac_esp32cam = models.CharField("MAC ESP32-CAM", max_length=17, blank=True)
    ip = models.GenericIPAddressField(protocol="IPv4", null=True, blank=True)
    version_firmware = models.CharField(max_length=10, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default="ACTIVO")
    ultimo_contacto = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "dispositivo"

    def __str__(self) -> str:
        return f"{self.codigo} ({self.vehiculo.placa})"

    @property
    def armado(self) -> bool:
        return self.estado == "ACTIVO"

    def esta_en_linea(self, timeout_s: int) -> bool:
        if self.ultimo_contacto is None:
            return False
        from django.utils import timezone

        return (timezone.now() - self.ultimo_contacto).total_seconds() <= timeout_s


class EstadoDispositivo(models.Model):
    """Heartbeat periódico -> DISPONIBILIDAD del sistema."""

    dispositivo = models.ForeignKey(
        Dispositivo, on_delete=models.CASCADE,
        related_name="estados", db_column="id_dispositivo",
    )
    fecha_hora = models.DateTimeField(auto_now_add=True, db_index=True)
    en_linea = models.BooleanField(default=True)
    rssi_wifi_dbm = models.SmallIntegerField("RSSI Wi-Fi (dBm)", null=True, blank=True)
    voltaje_v = models.FloatField("Voltaje (V)", null=True, blank=True)
    temperatura_c = models.FloatField("Temperatura (°C)", null=True, blank=True)
    ram_libre_kb = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "estado_dispositivo"
        ordering = ["-fecha_hora"]
        indexes = [models.Index(fields=["dispositivo", "fecha_hora"], name="idx_estado_disp_fecha")]
