from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from monitoreo.repositories.dispositivo_repository import DispositivoRepository
from monitoreo.repositories.incidencia_repository import IncidenciaRepository


class SelectorDispositivoMixin:
    """Resuelve de qué dispositivo se muestra el mapa en vivo (sección 5.6).

    Con un solo vehículo —el caso de la tesis— no hay nada que elegir: se usa el
    primero. Con varios, ``?codigo=NODO-02`` permite cambiar.
    """

    def _dispositivos_y_codigo(self):
        dispositivos = list(DispositivoRepository().todos())
        pedido = self.request.GET.get("codigo") or ""
        codigos = [d.codigo for d in dispositivos]
        if pedido in codigos:
            return dispositivos, pedido
        return dispositivos, codigos[0] if codigos else ""


class PanelController(SelectorDispositivoMixin, LoginRequiredMixin, TemplateView):
    """Panel en tiempo real (sección 9.3.2). La actualización en vivo viaja por WebSocket."""

    template_name = "monitoreo/panel/panel.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        incidencia_repo = IncidenciaRepository()

        dispositivos, codigo_mapa = self._dispositivos_y_codigo()
        contexto["kpis"] = incidencia_repo.kpis_hoy()
        contexto["dispositivos"] = dispositivos
        contexto["codigo_mapa"] = codigo_mapa
        contexto["incidencias_recientes"] = incidencia_repo.recientes(10)
        return contexto


class MapaVivoController(SelectorDispositivoMixin, LoginRequiredMixin, TemplateView):
    """Página "Mapa en vivo": el mismo mapa del panel, a pantalla completa."""

    template_name = "monitoreo/panel/mapa_vivo.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        dispositivos, codigo_mapa = self._dispositivos_y_codigo()
        contexto["dispositivos"] = dispositivos
        contexto["codigo_mapa"] = codigo_mapa
        # Marcadores de las incidencias recientes que sí tienen coordenadas.
        contexto["incidencias_con_mapa"] = [
            incidencia for incidencia in IncidenciaRepository().recientes(20)
            if incidencia.latitud is not None and incidencia.longitud is not None
        ]
        return contexto
