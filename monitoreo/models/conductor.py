from django.core.validators import RegexValidator
from django.db import models


class Conductor(models.Model):
    """Propietario/conductor del vehículo monitoreado."""

    dni = models.CharField(
        "DNI", max_length=8, unique=True,
        validators=[RegexValidator(r"^\d{8}$", "El DNI debe tener 8 dígitos")],
    )
    nombres = models.CharField(max_length=80)
    apellidos = models.CharField(max_length=80)
    telefono = models.CharField(
        "Teléfono", max_length=15,
        validators=[RegexValidator(r"^\+?\d{9,15}$", "Teléfono inválido")],
    )
    correo = models.EmailField(max_length=100, blank=True)
    telegram_chat_id = models.CharField(
        "Chat ID de Telegram", max_length=30, blank=True,
        help_text="Se obtiene cuando el conductor escribe /start al bot",
    )
    direccion = models.CharField("Dirección", max_length=150, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conductor"
        verbose_name_plural = "conductores"
        ordering = ["apellidos", "nombres"]

    def __str__(self) -> str:
        return f"{self.apellidos}, {self.nombres} ({self.dni})"

    @property
    def nombre_completo(self) -> str:
        return f"{self.nombres} {self.apellidos}"

    @property
    def tiene_telegram(self) -> bool:
        return bool(self.telegram_chat_id)
