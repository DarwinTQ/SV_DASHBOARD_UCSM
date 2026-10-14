from django.db import models

from .conductor import Conductor
from .incidencia import Incidencia


class AlertaTelegram(models.Model):
    """Registro de cada intento de notificación por Telegram."""

    ESTADOS = [("ENVIADO", "Enviado"), ("FALLIDO", "Fallido"), ("PENDIENTE", "Pendiente")]

    incidencia = models.ForeignKey(
        Incidencia, on_delete=models.CASCADE,
        related_name="alertas", db_column="id_incidencia",
    )
    conductor = models.ForeignKey(
        Conductor, on_delete=models.PROTECT,
        related_name="alertas", db_column="id_conductor",
    )
    chat_id = models.CharField(max_length=30)
    message_id = models.BigIntegerField(null=True, blank=True)
    mensaje = models.TextField()
    con_imagen = models.BooleanField(default=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)
    estado_envio = models.CharField(max_length=10, choices=ESTADOS, default="PENDIENTE")
    codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        db_table = "alerta_telegram"
        ordering = ["-fecha_envio"]

    def __str__(self) -> str:
        return f"Alerta #{self.pk} ({self.estado_envio})"

    @property
    def fue_exitosa(self) -> bool:
        return self.estado_envio == "ENVIADO"

    @property
    def destinatario_nombre(self) -> str:
        """Quién recibió esta alerta (propietario, familiar o admin), ya que
        ``conductor`` siempre apunta al dueño del vehículo, no al destinatario."""
        from config import config

        if self.chat_id in config.telegram.admin_chat_ids:
            return "Administrador"

        from .acceso_telegram import AccesoTelegram

        acceso = AccesoTelegram.objects.filter(
            chat_id=self.chat_id, vehiculo=self.incidencia.dispositivo.vehiculo_id,
        ).first()
        if acceso:
            return f"{acceso.nombres} ({acceso.get_rol_display()})"
        return self.conductor.nombre_completo
