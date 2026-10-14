"""/estado."""
from __future__ import annotations

from django.utils import timezone

from monitoreo.bot.formateador import FormateadorMensajes

from .base import ComandoBase


class ComandoEstado(ComandoBase):
    nombre = "estado"
    descripcion = "Estado del vehículo en este momento"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return

        from monitoreo.models import Incidencia
        from monitoreo.repositories.dispositivo_repository import DispositivoRepository
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        en_linea = DispositivoRepository().en_linea(dispositivo)
        lectura = EstadoEnVivo.instancia().obtener(dispositivo.codigo)

        inicio_hoy = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
        incidencias_hoy = Incidencia.objects.filter(dispositivo=dispositivo, fecha_hora__gte=inicio_hoy).count()
        ultima = Incidencia.objects.filter(dispositivo=dispositivo).select_related("tipo").order_by("-fecha_hora").first()

        texto = FormateadorMensajes().estado(dispositivo, en_linea, lectura, incidencias_hoy, ultima)
        ctx.cliente.send_message(ctx.chat_id, texto)
