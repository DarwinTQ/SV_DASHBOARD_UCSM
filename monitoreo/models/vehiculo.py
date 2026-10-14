from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from .conductor import Conductor


class Vehiculo(models.Model):
    """Vehículo particular equipado con el nodo IoT."""

    TIPOS = [
        ("SEDAN", "Sedán"), ("HATCHBACK", "Hatchback"), ("SUV", "SUV"),
        ("PICKUP", "Pick-up"), ("VAN", "Van"), ("OTRO", "Otro"),
    ]

    conductor = models.ForeignKey(
        Conductor, on_delete=models.PROTECT,
        related_name="vehiculos", db_column="id_conductor",
    )
    placa = models.CharField(
        max_length=10, unique=True,
        validators=[RegexValidator(r"^[A-Z0-9]{3}-?[A-Z0-9]{3}$", "Formato de placa: ABC-123")],
    )
    marca = models.CharField(max_length=40)
    modelo = models.CharField(max_length=40)
    anio = models.PositiveSmallIntegerField(
        "Año", validators=[MinValueValidator(1980), MaxValueValidator(2100)],
    )
    color = models.CharField(max_length=30)
    tipo = models.CharField(max_length=20, choices=TIPOS, default="SEDAN")
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "vehiculo"
        ordering = ["placa"]

    def save(self, *args, **kwargs):
        self.placa = self.placa.upper()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.placa} — {self.marca} {self.modelo} ({self.color})"
