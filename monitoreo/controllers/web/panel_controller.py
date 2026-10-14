from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from monitoreo.repositories.dispositivo_repository import DispositivoRepository
from monitoreo.repositories.incidencia_repository import IncidenciaRepository


class PanelController(LoginRequiredMixin, TemplateView):
    """Panel en tiempo real (sección 9.3.2). La actualización en vivo viaja por WebSocket."""

    template_name = "monitoreo/panel/panel.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        incidencia_repo = IncidenciaRepository()
        dispositivo_repo = DispositivoRepository()

        contexto["kpis"] = incidencia_repo.kpis_hoy()
        contexto["dispositivos"] = dispositivo_repo.todos()
        contexto["incidencias_recientes"] = incidencia_repo.recientes(10)
        return contexto
