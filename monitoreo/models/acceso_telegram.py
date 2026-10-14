from django.db import models

from .vehiculo import Vehiculo


class AccesoTelegram(models.Model):
    """Personas autorizadas a usar el bot para un vehículo (propietario y familiares)."""

    ROLES = [("PROPIETARIO", "Propietario"), ("FAMILIAR", "Familiar")]

    vehiculo = models.ForeignKey(
        Vehiculo, on_delete=models.CASCADE,
        related_name="accesos_telegram", db_column="id_vehiculo",
    )
    nombres = models.CharField(max_length=80)
    parentesco = models.CharField(max_length=30, blank=True)
    rol = models.CharField(max_length=12, choices=ROLES, default="FAMILIAR")
    chat_id = models.CharField(max_length=30, blank=True, db_index=True)
    usuario_telegram = models.CharField(max_length=64, blank=True)
    codigo_invitacion = models.CharField(max_length=8, unique=True, null=True, blank=True)
    invitacion_expira = models.DateTimeField(null=True, blank=True)
    puede_controlar = models.BooleanField(default=False)
    recibe_alertas = models.BooleanField(default=True)
    silenciado_hasta = models.DateTimeField(null=True, blank=True)
    activo = models.BooleanField(default=True)
    fecha_vinculacion = models.DateTimeField(null=True, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "acceso_telegram"
        constraints = [
            models.UniqueConstraint(
                fields=["vehiculo", "chat_id"], condition=~models.Q(chat_id=""),
                name="uq_acceso_vehiculo_chat",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.nombres} ({self.get_rol_display()}) — {self.vehiculo.placa}"

    @property
    def vinculado(self) -> bool:
        return bool(self.chat_id)

    @property
    def silenciado(self) -> bool:
        from django.utils import timezone

        return bool(self.silenciado_hasta and self.silenciado_hasta > timezone.now())

    @property
    def invitacion_vencida(self) -> bool:
        from django.utils import timezone

        return bool(self.invitacion_expira and self.invitacion_expira < timezone.now())
