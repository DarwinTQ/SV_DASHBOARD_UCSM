"""/ubicacion."""
from __future__ import annotations

from .base import ComandoBase


class ComandoUbicacion(ComandoBase):
    nombre = "ubicacion"
    descripcion = "Última ubicación GPS conocida"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        placa = ctx.args[0] if ctx.args else None
        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        self.responder_dispositivo(ctx, dispositivo)

    def responder_dispositivo(self, ctx, dispositivo) -> None:
        from monitoreo.services.estado_en_vivo import EstadoEnVivo
        from monitoreo.services.mapas_service import MapasService

        lectura = EstadoEnVivo.instancia().obtener(dispositivo.codigo)
        gps = lectura.ultimo_gps_valido if lectura else None
        if not gps:
            ctx.cliente.send_message(ctx.chat_id, "📍 Todavía no hay una ubicación GPS disponible.")
            return

        ctx.cliente.send_location(ctx.chat_id, gps["lat"], gps["lon"])
        texto = (
            f"🚗 {dispositivo.vehiculo.placa} · {gps.get('vel_kmh', 0)} km/h · {gps.get('sats', '—')} satélites\n"
            f"🔗 {MapasService().url_google_maps(gps['lat'], gps['lon'])}"
        )
        ctx.cliente.send_message(ctx.chat_id, texto)
