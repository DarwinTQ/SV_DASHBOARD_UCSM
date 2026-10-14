"""Autenticación por API key para los endpoints del firmware (ESP32-S3)."""
import hmac
import logging

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import APIException, AuthenticationFailed, ValidationError

from config import config

logger = logging.getLogger("monitoreo.security")


class DispositivoNoEncontradoError(APIException):
    status_code = 404
    default_detail = "dispositivo no registrado"
    default_code = "dispositivo_no_registrado"


class AutenticacionApiKey(BaseAuthentication):
    """Compara la cabecera ``X-API-Key`` contra ``config.esp32.api_key``.

    Usa ``hmac.compare_digest`` para evitar ataques de temporización. También
    registra (sin bloquear) si ``X-Device-Code`` no coincide con el ``codigo``
    del cuerpo de la petición, porque esa cabecera es solo informativa.
    """

    def authenticate(self, request):
        clave_recibida = request.META.get("HTTP_X_API_KEY", "")
        clave_esperada = config.esp32.api_key
        if not clave_recibida or not hmac.compare_digest(clave_recibida, clave_esperada):
            raise AuthenticationFailed("API key inválida")

        codigo_cabecera = request.META.get("HTTP_X_DEVICE_CODE")
        codigo_cuerpo = None
        if hasattr(request, "data") and isinstance(request.data, dict):
            codigo_cuerpo = request.data.get("codigo")
        if codigo_cabecera and codigo_cuerpo and codigo_cabecera != codigo_cuerpo:
            logger.warning(
                "X-Device-Code (%s) no coincide con el 'codigo' del cuerpo (%s)",
                codigo_cabecera, codigo_cuerpo,
            )

        return (None, None)

    def authenticate_header(self, request):
        return "X-API-Key"


def manejador_excepciones_api(exc, context):
    """Da forma uniforme ``{"ok": false, ...}`` a los errores de la API."""
    from rest_framework.views import exception_handler

    respuesta = exception_handler(exc, context)
    if respuesta is None:
        return respuesta

    if isinstance(exc, ValidationError):
        respuesta.data = {"ok": False, "errores": respuesta.data}
    else:
        detalle = respuesta.data.get("detail", respuesta.data) if isinstance(respuesta.data, dict) else respuesta.data
        respuesta.data = {"ok": False, "error": str(detalle)}
    return respuesta
