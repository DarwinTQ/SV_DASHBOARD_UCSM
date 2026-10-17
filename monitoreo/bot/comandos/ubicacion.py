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
        """Usa el último punto del rastro de ``EstadoEnVivo``, que el nodo
        alimenta a 5 Hz en marcha por el tópico MQTT ``gps`` (sección 5.6)."""
        from monitoreo.services.estado_en_vivo import EstadoEnVivo
        from monitoreo.services.mapas_service import MapasService

        estado_en_vivo = EstadoEnVivo.instancia()
        punto = estado_en_vivo.ultimo_punto_gps(dispositivo.codigo)
        if punto is None:
            # Respaldo: el bloque "gps" de la última telemetría (1 Hz).
            lectura = estado_en_vivo.obtener(dispositivo.codigo)
            gps = lectura.ultimo_gps_valido if lectura else None
            if not gps:
                ctx.cliente.send_message(ctx.chat_id, "📍 Todavía no hay una ubicación GPS disponible.")
                return
            ctx.cliente.send_location(ctx.chat_id, gps["lat"], gps["lon"])
            ctx.cliente.send_message(ctx.chat_id, "\n".join([
                f"🚗 {dispositivo.vehiculo.placa} · {gps.get('vel_kmh', 0)} km/h · "
                f"{gps.get('sats', '—')} satélites",
                f"🔗 {MapasService().url_google_maps(gps['lat'], gps['lon'])}",
            ]))
            return

        ctx.cliente.send_location(ctx.chat_id, punto.lat, punto.lon)
        modo = punto.modo or estado_en_vivo.modo(dispositivo.codigo)
        lineas = [
            f"🚗 <b>{dispositivo.vehiculo.placa}</b> · {punto.vel_kmh} km/h · rumbo {punto.rumbo_deg:.0f}°",
            f"🛰️ {punto.sats if punto.sats is not None else '—'} satélites · "
            f"HDOP {punto.hdop if punto.hdop is not None else '—'}",
        ]
        if modo:
            lineas.append(f"🚦 {'EN MARCHA' if modo == 'EN_MARCHA' else 'ESTACIONADO'}")
        lineas.append(f"🕒 Dato de hace {punto.a_dict()['antiguedad_s']} s")
        lineas.append(f"🔗 {MapasService().url_google_maps(punto.lat, punto.lon)}")
        ctx.cliente.send_message(ctx.chat_id, "\n".join(lineas))
