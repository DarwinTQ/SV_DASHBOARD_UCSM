"""Patrón registro: mapea nombres de comando / botones de texto a instancias."""
from __future__ import annotations


class RegistroComandos:
    def __init__(self):
        from .alertas import ComandoAlertas, ComandoSilenciar
        from .control import ComandoArmar, ComandoDesarmar
        from .estado import ComandoEstado
        from .familia import ComandoFamilia, ComandoInvitar, ComandoRevocar
        from .foto import ComandoFoto
        from .incidencias import ComandoIncidencias, ComandoUltima
        from .info import ComandoMetricas, ComandoVehiculo
        from .inicio import ComandoAyuda, ComandoInicio
        from .sensores import ComandoBateria, ComandoSensores
        from .ubicacion import ComandoUbicacion
        from .vinculacion import ComandoSalir, ComandoUnirse, ComandoVincular

        instancias = [
            ComandoInicio(), ComandoAyuda(),
            ComandoVincular(), ComandoUnirse(), ComandoSalir(),
            ComandoInvitar(), ComandoFamilia(), ComandoRevocar(),
            ComandoEstado(), ComandoFoto(), ComandoUbicacion(),
            ComandoSensores(), ComandoBateria(),
            ComandoUltima(), ComandoIncidencias(),
            ComandoArmar(), ComandoDesarmar(),
            ComandoAlertas(), ComandoSilenciar(),
            ComandoVehiculo(), ComandoMetricas(),
        ]
        self._por_nombre = {c.nombre: c for c in instancias}
        self._alias_botones = {
            "📊 Estado": "estado", "📷 Foto": "foto", "📍 Ubicación": "ubicacion",
            "🔒 Armar": "armar", "🔓 Desarmar": "desarmar", "🚨 Última": "ultima",
            "🛰️ Sensores": "sensores", "👪 Familia": "familia", "❓ Ayuda": "ayuda",
        }

    def obtener(self, nombre: str):
        return self._por_nombre.get(nombre)

    def resolver_desde_texto(self, texto: str):
        """Devuelve ``(comando, args)``; ``comando`` es ``None`` si no se reconoce."""
        texto = texto.strip()
        if texto.startswith("/"):
            partes = texto.split()
            cuerpo = partes[0][1:].split("@")[0].lower()
            return self._por_nombre.get(cuerpo), partes[1:]

        nombre = self._alias_botones.get(texto)
        if nombre:
            return self._por_nombre.get(nombre), []
        return None, []

    def lista_para_set_my_commands(self):
        return [(c.nombre, c.descripcion) for c in self._por_nombre.values() if c.descripcion]
