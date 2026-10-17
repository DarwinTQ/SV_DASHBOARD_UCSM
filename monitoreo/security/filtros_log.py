"""Filtros de logging para el ruido que no indica ningún fallo real."""
import asyncio
import logging


class FiltroDesconexionCliente(logging.Filter):
    """Descarta el traceback de asyncio cuando un cliente corta la petición.

    Cuando el navegador abandona una petición a medias (una recarga, un cambio
    de página, el sondeo del panel que se cierra), Daphne cancela la tarea y
    ``asyncio.shield`` avisa al manejador de excepciones del bucle con el
    mensaje "CancelledError exception in shielded future" (ver
    ``asyncio/tasks.py``). No hay nada roto: simplemente ya nadie espera esa
    respuesta. Cualquier otro error de asyncio se sigue registrando.
    """

    MARCA = "exception in shielded future"
    IGNORABLES = (asyncio.CancelledError, ConnectionError)

    def filter(self, record: logging.LogRecord) -> bool:
        if self.MARCA not in record.getMessage():
            return True
        excepcion = record.exc_info[1] if record.exc_info else None
        return not isinstance(excepcion, self.IGNORABLES)
