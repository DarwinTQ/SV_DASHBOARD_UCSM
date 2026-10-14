"""JSON para el frontend autenticado por sesión (sección 10)."""
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from config import config
from monitoreo.models import Dispositivo
from monitoreo.repositories.dispositivo_repository import DispositivoRepository
from monitoreo.repositories.incidencia_repository import IncidenciaRepository
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal


class PanelApiBase(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]


class PanelEstadoApi(PanelApiBase):
    """Estado en vivo de todos los dispositivos (fallback de sondeo si el WS no conecta)."""

    def get(self, request):
        estado_en_vivo = EstadoEnVivo.instancia()
        repo = DispositivoRepository()
        resultado = []
        for dispositivo in repo.todos():
            lectura = estado_en_vivo.obtener(dispositivo.codigo)
            resultado.append({
                "codigo": dispositivo.codigo, "placa": dispositivo.vehiculo.placa,
                "armado": dispositivo.armado, "en_linea": repo.en_linea(dispositivo),
                "ultimo_contacto": dispositivo.ultimo_contacto.isoformat() if dispositivo.ultimo_contacto else None,
                "ultima_telemetria": lectura.ultima_telemetria if lectura else None,
                "camara_estado": lectura.camara_estado if lectura else None,
                "ultimo_frame_url": lectura.ultimo_frame_url if lectura else None,
                "fps_vision": lectura.fps_vision if lectura else None,
                "personas_detectadas": lectura.personas_detectadas if lectura else 0,
            })
        return Response({"ok": True, "dispositivos": resultado})


class PanelIncidenciasRecientesApi(PanelApiBase):
    """Últimas incidencias para el feed del panel; con filtros opcionales
    (desde/hasta/tipo) también alimenta el mapa de incidencias."""

    def get(self, request):
        parametros = request.query_params
        repo = IncidenciaRepository()
        if parametros.get("desde") or parametros.get("hasta") or parametros.get("tipo"):
            qs = repo.filtrar(
                desde=parametros.get("desde") or None, hasta=parametros.get("hasta") or None,
                tipo=parametros.get("tipo") or None,
            ).order_by("-fecha_hora")[:200]
        else:
            qs = repo.recientes(10)

        datos = [{
            "id": inc.pk, "tipo": inc.tipo.codigo, "nombre_tipo": inc.tipo.nombre,
            "riesgo": inc.tipo.nivel_riesgo, "placa": inc.dispositivo.vehiculo.placa,
            "fecha_hora": inc.fecha_hora.isoformat(), "estado": inc.estado,
            "miniatura": (inc.imagen_anotada.url if inc.imagen_anotada else (inc.imagen.url if inc.imagen else None)),
            "lat": float(inc.latitud) if inc.latitud is not None else None,
            "lon": float(inc.longitud) if inc.longitud is not None else None,
        } for inc in qs]
        return Response({"ok": True, "incidencias": datos})


class PanelMetricasApi(PanelApiBase):
    """Estadísticas completas (sección 8.6), usadas por la página Resultados."""

    def get(self, request):
        from monitoreo.services.metricas_service import MetricasService

        parametros = request.query_params
        resultados = MetricasService().resultados_completos(
            desde=parametros.get("desde") or None, hasta=parametros.get("hasta") or None,
            dispositivo_id=parametros.get("dispositivo") or None, tipo=parametros.get("tipo") or None,
        )
        return Response({"ok": True, **resultados})


class DispositivoArmarApi(PanelApiBase):
    def post(self, request, pk):
        try:
            dispositivo = Dispositivo.objects.get(pk=pk)
        except Dispositivo.DoesNotExist:
            return Response({"ok": False, "error": "dispositivo no encontrado"}, status=404)

        armado = bool(request.data.get("armado"))
        dispositivo.estado = "ACTIVO" if armado else "INACTIVO"
        dispositivo.save(update_fields=["estado"])
        NotificadorTiempoReal.dispositivo_estado({"codigo": dispositivo.codigo, "armado": dispositivo.armado})
        return Response({"ok": True, "armado": dispositivo.armado})
