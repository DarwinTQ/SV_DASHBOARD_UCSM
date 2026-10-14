"""Página de Resultados y métricas (sección 8.6 / 9.3.7)."""
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.views.generic import TemplateView, View

from monitoreo.bot.metricas_bot_service import MetricasBotService
from monitoreo.models import Dispositivo, TipoEvento
from monitoreo.services.exportacion_service import ExportacionService
from monitoreo.services.metricas_service import MetricasService


def _filtros_desde_request(request) -> dict:
    return {
        "desde": request.GET.get("desde") or None,
        "hasta": request.GET.get("hasta") or None,
        "dispositivo_id": request.GET.get("dispositivo") or None,
        "tipo": request.GET.get("tipo") or None,
    }


ETIQUETAS_TIEMPOS = [
    ("Latencia de red", "latencia_red_ms"),
    ("Tiempo de captura", "tiempo_captura_ms"),
    ("Tiempo de inferencia (YOLO)", "tiempo_inferencia_ms"),
    ("Tiempo de registro en BD", "tiempo_bd_ms"),
    ("Latencia de Telegram", "latencia_telegram_ms"),
    ("Tiempo de respuesta total", "tiempo_respuesta_total_ms"),
    ("FPS", "fps"),
]


class ResultadosController(LoginRequiredMixin, TemplateView):
    template_name = "monitoreo/resultados/resultados.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        filtros = _filtros_desde_request(self.request)
        resultados = MetricasService().resultados_completos(**filtros)

        contexto["dispositivos"] = Dispositivo.objects.all()
        contexto["tipos_evento"] = TipoEvento.objects.all()
        contexto["filtros"] = filtros
        contexto["resultados"] = resultados
        contexto["filas_tiempos"] = [(etiqueta, resultados["tiempos"][clave]) for etiqueta, clave in ETIQUETAS_TIEMPOS]
        contexto["consultas_bot"] = MetricasBotService().resumen_por_comando()
        contexto["entrega_alertas"] = MetricasService().resumen_entrega_alertas(
            desde=filtros["desde"], hasta=filtros["hasta"], dispositivo_id=filtros["dispositivo_id"],
        )
        return contexto


class ExportarExcelController(LoginRequiredMixin, View):
    """GET /resultados/exportar-excel/ — descarga el libro para la tesis."""

    def get(self, request):
        filtros = _filtros_desde_request(request)
        contenido = ExportacionService().generar_excel(**filtros)
        respuesta = HttpResponse(
            contenido, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        respuesta["Content-Disposition"] = 'attachment; filename="resultados_monitoreo_ucsm.xlsx"'
        return respuesta
