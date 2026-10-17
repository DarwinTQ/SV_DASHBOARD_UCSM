"""Qué hace el servidor con cada incidencia, según ``tipo_evento.nivel_riesgo``.

Sección 5.4 del contrato: la acción **no** la decide el firmware, la decide el
catálogo de la base de datos. El ``nivel`` que envía el nodo es una *propuesta*
que se valida contra el catálogo y se registra en ``observacion``; así el
tesista puede cambiar la política desde la página "Tipos de evento" sin tocar
el firmware ni volver a flashear el ESP32.

    nivel_riesgo   acción
    ------------   --------------------------------------------------------
    ALTO           incidencia + alerta de Telegram con foto a todos
    MEDIO          incidencia + alerta SILENCIOSA (disable_notification)
    BAJO           solo incidencia (registro); nada de Telegram
"""
from __future__ import annotations

from dataclasses import dataclass

# Mapa nivel_riesgo -> nivel que el firmware habría propuesto.
NIVEL_POR_RIESGO = {"ALTO": "ALERTA", "MEDIO": "ALERTA_LEVE", "BAJO": "REGISTRO"}


@dataclass(frozen=True)
class AccionIncidencia:
    """Decisión ya tomada para una incidencia concreta."""

    alertar: bool
    silenciosa: bool
    nivel_riesgo: str
    nivel_catalogo: str

    @property
    def descripcion(self) -> str:
        if not self.alertar:
            return "solo registro (sin Telegram)"
        return "alerta silenciosa" if self.silenciosa else "alerta con notificación"


class ReglasAccion:
    """Traduce el ``nivel_riesgo`` del catálogo en una acción concreta."""

    @staticmethod
    def para(tipo_evento) -> AccionIncidencia:
        riesgo = (tipo_evento.nivel_riesgo or "BAJO").upper()
        return AccionIncidencia(
            alertar=riesgo in ("ALTO", "MEDIO"),
            silenciosa=riesgo == "MEDIO",
            nivel_riesgo=riesgo,
            nivel_catalogo=NIVEL_POR_RIESGO.get(riesgo, "REGISTRO"),
        )

    @staticmethod
    def validar_nivel_firmware(tipo_evento, nivel_firmware: str | None) -> str:
        """Compara el ``nivel`` propuesto por el nodo con el del catálogo.

        Devuelve el texto que se guarda en ``observacion``: vacío si coinciden,
        y una nota explícita si no, porque una discrepancia casi siempre
        significa que el firmware y el catálogo se desincronizaron.
        """
        if not nivel_firmware:
            return ""
        accion = ReglasAccion.para(tipo_evento)
        if nivel_firmware == accion.nivel_catalogo:
            return f"Nivel del nodo: {nivel_firmware} (coincide con el catálogo)."
        return (
            f"Nivel del nodo: {nivel_firmware}, pero el catálogo marca "
            f"{tipo_evento.nivel_riesgo} → {accion.nivel_catalogo}; manda el catálogo."
        )


class DescriptorEvidencia:
    """Convierte el bloque ``evidencia`` del evento en texto legible.

    Se usa en dos sitios: ``observacion`` de la incidencia (texto completo, para
    el Excel de resultados) y el mensaje de Telegram (resumen corto).
    """

    @staticmethod
    def texto_completo(tipo_codigo: str, evidencia: dict | None) -> str:
        if not evidencia:
            return ""
        partes = []
        if tipo_codigo in ("CHOQUE", "IMPACTO_SIN_CONFIRMAR"):
            if evidencia.get("a_pico_g") is not None:
                partes.append(f"pico {evidencia['a_pico_g']} g")
            if evidencia.get("spl_pico_db"):
                partes.append(f"sonido {evidencia['spl_pico_db']} dB SPL")
            delta = evidencia.get("delta_ms")
            partes.append(
                f"desfase sonido-golpe {delta} ms" if delta is not None
                else "sin golpe sonoro en la ventana de 500 ms"
            )
        else:
            if evidencia.get("e_baja_db") is not None:
                partes.append(
                    f"banda 150–400 Hz {evidencia['e_baja_db']} dB "
                    f"(piso {evidencia.get('piso_baja_db')} dB)"
                )
            if evidencia.get("e_alta_db") is not None:
                partes.append(
                    f"banda 3–7 kHz {evidencia['e_alta_db']} dB "
                    f"(piso {evidencia.get('piso_alta_db')} dB)"
                )
            if evidencia.get("planitud") is not None:
                partes.append(f"planitud {evidencia['planitud']}")
            if evidencia.get("duracion_ms"):
                partes.append(f"estallido {evidencia['duracion_ms']} ms")
            if evidencia.get("vib_g"):
                partes.append(f"vibración {evidencia['vib_g']} g")
            delta_vib = evidencia.get("delta_vib_ms")
            if delta_vib is not None:
                partes.append(f"desfase vibración-golpe {delta_vib} ms")
        if not partes:
            return ""
        return f"Evidencia: {', '.join(partes)}."

    @staticmethod
    def resumen_corto(tipo_codigo: str, evidencia: dict | None) -> str:
        """Una línea para el texto de Telegram (sección 5.4)."""
        if not evidencia:
            return ""
        if tipo_codigo in ("CHOQUE", "IMPACTO_SIN_CONFIRMAR"):
            pico = evidencia.get("a_pico_g")
            spl = evidencia.get("spl_pico_db")
            delta = evidencia.get("delta_ms")
            if pico and spl and delta is not None:
                return f"{pico} g + {spl} dB en {delta} ms"
            if pico:
                return f"{pico} g sin golpe sonoro confirmado"
            return ""
        duracion = evidencia.get("duracion_ms")
        planitud = evidencia.get("planitud")
        vib = evidencia.get("vib_g")
        if duracion and planitud is not None:
            texto = f"estallido {duracion} ms, planitud {planitud}"
            return f"{texto}, vibración {vib} g" if vib else texto
        if vib:
            return f"vibración {vib} g sin estallido de vidrio"
        return ""
