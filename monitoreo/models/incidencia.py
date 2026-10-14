from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from .dispositivo import Dispositivo
from .evento import TipoEvento


class Incidencia(models.Model):
    """Evento de riesgo registrado, con su imagen y resultado de validación."""

    ESTADOS = [("PENDIENTE", "Pendiente"), ("NOTIFICADA", "Notificada"), ("ATENDIDA", "Atendida")]
    VALIDACION = [
        ("VP", "Verdadero positivo"), ("FP", "Falso positivo"),
        ("FN", "Falso negativo"), ("VN", "Verdadero negativo"),
    ]

    dispositivo = models.ForeignKey(
        Dispositivo, on_delete=models.PROTECT,
        related_name="incidencias", db_column="id_dispositivo",
    )
    tipo = models.ForeignKey(
        TipoEvento, on_delete=models.PROTECT,
        related_name="incidencias", db_column="id_tipo",
    )
    fecha_hora = models.DateTimeField(db_index=True)
    aceleracion_g = models.FloatField("|a| (g)", null=True, blank=True)
    nivel_audio_db = models.FloatField("Nivel de audio (dB)", null=True, blank=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    imagen = models.ImageField(upload_to="capturas/%Y/%m/", null=True, blank=True)
    imagen_anotada = models.ImageField(
        upload_to="anotadas/%Y/%m/", null=True, blank=True,
        help_text="Imagen con las cajas de YOLO dibujadas",
    )
    estado = models.CharField(max_length=15, choices=ESTADOS, default="PENDIENTE")
    validacion = models.CharField(max_length=2, choices=VALIDACION, null=True, blank=True)
    observacion = models.TextField(blank=True)

    class Meta:
        db_table = "incidencia"
        ordering = ["-fecha_hora"]
        indexes = [models.Index(fields=["dispositivo", "fecha_hora"], name="idx_incidencia_disp_fecha")]

    def __str__(self) -> str:
        return f"#{self.pk} {self.tipo} — {self.dispositivo.vehiculo.placa} — {self.fecha_hora:%d/%m/%Y %H:%M:%S}"

    @property
    def link_mapa(self) -> str:
        if self.latitud is None:
            return ""
        return f"https://maps.google.com/?q={self.latitud},{self.longitud}"

    @property
    def tiene_coordenadas(self) -> bool:
        return self.latitud is not None and self.longitud is not None

    @property
    def esta_validada(self) -> bool:
        return self.validacion is not None


class DeteccionYolo(models.Model):
    """Una caja detectada por YOLO dentro de una incidencia."""

    incidencia = models.ForeignKey(
        Incidencia, on_delete=models.CASCADE,
        related_name="detecciones", db_column="id_incidencia",
    )
    clase = models.CharField(max_length=30)
    confianza = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    x1 = models.IntegerField()
    y1 = models.IntegerField()
    x2 = models.IntegerField()
    y2 = models.IntegerField()
    modelo_yolo = models.CharField(max_length=30, default="yolo26n")

    class Meta:
        db_table = "deteccion_yolo"

    def __str__(self) -> str:
        return f"{self.clase} ({self.confianza:.0%})"
