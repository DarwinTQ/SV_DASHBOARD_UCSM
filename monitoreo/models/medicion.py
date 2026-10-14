from django.db import models

from .incidencia import Incidencia


class MedicionTiempo(models.Model):
    """Marcas de tiempo de cada etapa del pipeline, para el Capítulo V de la tesis."""

    incidencia = models.OneToOneField(
        Incidencia, on_delete=models.CASCADE,
        related_name="tiempos", db_column="id_incidencia",
    )
    t_deteccion_sensor = models.DateTimeField(help_text="Cuando el sensor detecta el evento")
    t_recepcion_servidor = models.DateTimeField(help_text="Cuando Django recibe la petición")
    t_inicio_inferencia = models.DateTimeField(null=True, blank=True)
    t_fin_inferencia = models.DateTimeField(null=True, blank=True)
    t_registro_bd = models.DateTimeField(null=True, blank=True)
    t_envio_telegram = models.DateTimeField(null=True, blank=True)
    t_confirmacion_telegram = models.DateTimeField(null=True, blank=True)
    latencia_red_ms = models.FloatField(null=True, blank=True)
    tiempo_inferencia_ms = models.FloatField(null=True, blank=True)
    tiempo_bd_ms = models.FloatField(null=True, blank=True)
    latencia_telegram_ms = models.FloatField(null=True, blank=True)
    tiempo_respuesta_total_ms = models.FloatField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "medicion_tiempo"

    @staticmethod
    def _ms(a, b):
        return round((b - a).total_seconds() * 1000, 2) if a and b else None

    def save(self, *args, **kwargs):
        sin_reloj_sensor = getattr(self, "_sin_reloj_sensor", False)
        if sin_reloj_sensor:
            self.latencia_red_ms = None
            self.tiempo_respuesta_total_ms = self._ms(self.t_recepcion_servidor, self.t_confirmacion_telegram)
        else:
            self.latencia_red_ms = self._ms(self.t_deteccion_sensor, self.t_recepcion_servidor)
            self.tiempo_respuesta_total_ms = self._ms(self.t_deteccion_sensor, self.t_confirmacion_telegram)
        self.tiempo_inferencia_ms = self._ms(self.t_inicio_inferencia, self.t_fin_inferencia)
        self.tiempo_bd_ms = self._ms(self.t_fin_inferencia, self.t_registro_bd)
        self.latencia_telegram_ms = self._ms(self.t_envio_telegram, self.t_confirmacion_telegram)
        self.fps = round(1000 / self.tiempo_inferencia_ms, 2) if self.tiempo_inferencia_ms else None
        super().save(*args, **kwargs)

    @property
    def tiempo_captura_ms(self):
        return self._ms(self.t_recepcion_servidor, self.t_inicio_inferencia)
