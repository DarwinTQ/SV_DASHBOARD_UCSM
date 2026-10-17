"""Middleware de seguridad y medición de tiempos para la API del firmware."""
import time

from asgiref.sync import iscoroutinefunction, markcoroutinefunction
from django.http import JsonResponse

LIMITE_BODY_API_BYTES = 64 * 1024
RUTAS_API_FIRMWARE = ("/api/v1/telemetria/", "/api/v1/eventos/")


class MarcaTiempoRecepcionMiddleware:
    """La PRIMERA cosa que hace cada request: marca ``request.t_recepcion_ms``.

    Ese valor es ``t_recepcion_servidor`` y también el ``server_ts_ms`` que se
    devuelve al firmware para que sincronice su reloj.

    Soporta las dos cadenas (sync y async) a propósito: bajo Daphne el handler
    es async, y si este middleware fuera solo sync Django envolvería TODA la
    cadena en ``sync_to_async`` + ``async_to_sync``. Ese doble rebote gasta un
    hilo por petición y, cuando el cliente se desconecta a medias, llena la
    consola de tracebacks "CancelledError exception in shielded future".
    """

    async_capable = True
    sync_capable = True

    def __init__(self, get_response):
        self.get_response = get_response
        self._asincrono = iscoroutinefunction(get_response)
        if self._asincrono:
            markcoroutinefunction(self)

    def __call__(self, request):
        if self._asincrono:
            return self._llamar_asincrono(request)
        rechazo = self._marcar_y_validar(request)
        return rechazo if rechazo is not None else self.get_response(request)

    async def _llamar_asincrono(self, request):
        rechazo = self._marcar_y_validar(request)
        if rechazo is not None:
            return rechazo
        return await self.get_response(request)

    def _marcar_y_validar(self, request):
        """Sella la hora de recepción y corta los cuerpos demasiado grandes."""
        request.t_recepcion_ms = time.time_ns() // 1_000_000

        if request.path in RUTAS_API_FIRMWARE:
            content_length = request.META.get("CONTENT_LENGTH")
            if content_length and int(content_length) > LIMITE_BODY_API_BYTES:
                return JsonResponse(
                    {"ok": False, "error": "cuerpo de la petición demasiado grande"},
                    status=413,
                )
        return None
