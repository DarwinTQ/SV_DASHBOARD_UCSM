"""Construye los teclados (persistente y en línea) del bot (sección 6)."""
from __future__ import annotations


class FabricaTeclados:
    """Cada método devuelve el dict listo para ``reply_markup``."""

    def menu_principal(self, puede_controlar: bool, puede_invitar: bool) -> dict:
        filas = [
            [{"text": "📊 Estado"}, {"text": "📷 Foto"}, {"text": "📍 Ubicación"}],
        ]
        fila_control = []
        if puede_controlar:
            fila_control = [{"text": "🔒 Armar"}, {"text": "🔓 Desarmar"}]
        fila_control.append({"text": "🚨 Última"})
        filas.append(fila_control)

        fila_final = [{"text": "🛰️ Sensores"}]
        if puede_invitar:
            fila_final.append({"text": "👪 Familia"})
        fila_final.append({"text": "❓ Ayuda"})
        filas.append(fila_final)

        return {"keyboard": filas, "resize_keyboard": True}

    def seleccion_vehiculo(self, vehiculos, prefijo_callback: str) -> dict:
        filas = [
            [{"text": f"🚗 {v.placa}", "callback_data": f"sel:{prefijo_callback}:{v.pk}"}]
            for v in vehiculos
        ]
        return {"inline_keyboard": filas}

    def alerta(self, incidencia, puede_controlar: bool) -> dict:
        filas = [
            [
                {"text": "✅ Es real", "callback_data": f"v:{incidencia.pk}:VP"},
                {"text": "❌ Falsa alarma", "callback_data": f"v:{incidencia.pk}:FP"},
            ],
            [
                {"text": "📷 Foto ahora", "callback_data": f"f:{incidencia.dispositivo_id}"},
                {"text": "📍 Ubicación", "callback_data": f"u:{incidencia.dispositivo_id}"},
            ],
        ]
        if puede_controlar:
            filas.append([{"text": "🔓 Desarmar", "callback_data": f"d:{incidencia.dispositivo_id}"}])
        return {"inline_keyboard": filas}

    def validada(self, texto: str) -> dict:
        return {"inline_keyboard": [[{"text": texto, "callback_data": "ok:info"}]]}

    def confirmar_control(self, accion: str, dispositivo_id: int) -> dict:
        return {
            "inline_keyboard": [[
                {"text": "✅ Confirmar", "callback_data": f"ok:{accion}:{dispositivo_id}"},
                {"text": "✖ Cancelar", "callback_data": "ok:cancelar:0"},
            ]],
        }

    def foto(self, dispositivo_id: int) -> dict:
        return {"inline_keyboard": [[{"text": "🔄 Otra foto", "callback_data": f"f:{dispositivo_id}"}]]}

    def familia(self, accesos, es_propietario: bool) -> dict:
        filas = []
        for acceso in accesos:
            if acceso.rol == "PROPIETARIO":
                continue
            fila = []
            if es_propietario:
                fila.append({"text": f"🚫 Revocar a {acceso.nombres}", "callback_data": f"rv:{acceso.pk}"})
                texto_permiso = "🔓 Quitar control" if acceso.puede_controlar else "🔑 Permitir control"
                fila.append({"text": f"{texto_permiso} ({acceso.nombres})", "callback_data": f"pc:{acceso.pk}"})
            if fila:
                filas.append(fila)
        return {"inline_keyboard": filas} if filas else {}
