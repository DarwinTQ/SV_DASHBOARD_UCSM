"""Middleware de seguridad y medición de tiempos para la API del firmware."""
import time

from django.http import JsonResponse

LIMITE_BODY_API_BYTES = 64 * 1024
RUTAS_API_FIRMWARE = ("/api/v1/telemetria/", "/api/v1/eventos/")


class MarcaTiempoRecepcionMiddleware:
    """La PRIMERA cosa que hace cada request: marca ``request.t_recepcion_ms``.

    Ese valor es ``t_recepcion_servidor`` y también el ``server_ts_ms`` que se
    devuelve al firmware para que sincronice su reloj.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.t_recepcion_ms = time.time_ns() // 1_000_000

        if request.path in RUTAS_API_FIRMWARE:
            content_length = request.META.get("CONTENT_LENGTH")
            if content_length and int(content_length) > LIMITE_BODY_API_BYTES:
                return JsonResponse(
                    {"ok": False, "error": "cuerpo de la petición demasiado grande"},
                    status=413,
                )

        return self.get_response(request)
