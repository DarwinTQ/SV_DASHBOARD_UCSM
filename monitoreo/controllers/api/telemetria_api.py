"""POST /api/v1/telemetria/ — contrato fijo, sección 7.2."""
from rest_framework.response import Response
from rest_framework.views import APIView

from config import config
from monitoreo.models import Dispositivo
from monitoreo.security.api_key import AutenticacionApiKey, DispositivoNoEncontradoError
from monitoreo.serializers import TelemetriaSerializer
from monitoreo.services.telemetria_service import TelemetriaService


class TelemetriaApi(APIView):
    authentication_classes = [AutenticacionApiKey]
    permission_classes = []

    def post(self, request):
        serializer = TelemetriaSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        dispositivo = Dispositivo.objects.filter(codigo=datos["codigo"]).first()
        if dispositivo is None:
            raise DispositivoNoEncontradoError()

        TelemetriaService().procesar(dispositivo, datos)

        return Response({
            "ok": True,
            "server_ts_ms": request.t_recepcion_ms,
            "armado": dispositivo.armado,
            "umbrales": config.umbrales.a_dict(),
        })
