"""POST /api/v1/eventos/ — contrato fijo, sección 7.3."""
from rest_framework.response import Response
from rest_framework.views import APIView

from monitoreo.models import Dispositivo
from monitoreo.security.api_key import AutenticacionApiKey, DispositivoNoEncontradoError
from monitoreo.serializers import EventoSerializer
from monitoreo.workers.procesador_eventos import ProcesadorEventos


class EventosApi(APIView):
    authentication_classes = [AutenticacionApiKey]
    permission_classes = []

    def post(self, request):
        serializer = EventoSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        datos = serializer.validated_data

        dispositivo = Dispositivo.objects.filter(codigo=datos["codigo"]).first()
        if dispositivo is None:
            raise DispositivoNoEncontradoError()

        datos["t_recepcion_ms"] = request.t_recepcion_ms
        resultado = ProcesadorEventos.instancia().encolar(datos)

        if resultado.duplicado:
            return Response({
                "ok": True,
                "duplicado": True,
                "evento_uid": datos["evento_uid"],
                "server_ts_ms": request.t_recepcion_ms,
            }, status=200)

        if not resultado.encolado:
            return Response({
                "ok": False,
                "error": "cola de eventos llena, reintente",
                "evento_uid": datos["evento_uid"],
                "server_ts_ms": request.t_recepcion_ms,
            }, status=503)

        return Response({
            "ok": True,
            "encolado": True,
            "evento_uid": datos["evento_uid"],
            "server_ts_ms": request.t_recepcion_ms,
        }, status=202)
