from django.db import models


class TipoEvento(models.Model):
    """Catálogo de tipos de evento detectables por el nodo o por YOLO."""

    RIESGO = [("BAJO", "Bajo"), ("MEDIO", "Medio"), ("ALTO", "Alto")]

    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=60)
    nivel_riesgo = models.CharField(max_length=10, choices=RIESGO, default="MEDIO")
    descripcion = models.TextField(blank=True)

    class Meta:
        db_table = "tipo_evento"
        ordering = ["codigo"]

    def __str__(self) -> str:
        return self.nombre
