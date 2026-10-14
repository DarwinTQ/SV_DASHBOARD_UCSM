from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.shortcuts import get_object_or_404, redirect
from django.views.generic import ListView, View

from monitoreo.models import AlertaTelegram
from monitoreo.services.telegram_service import TelegramService


class AlertaListController(LoginRequiredMixin, ListView):
    template_name = "monitoreo/alertas/lista.html"
    context_object_name = "alertas"
    paginate_by = 20

    def get_queryset(self):
        qs = AlertaTelegram.objects.select_related("incidencia", "conductor")
        estado = self.request.GET.get("estado")
        if estado:
            qs = qs.filter(estado_envio=estado)
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["estado_filtro"] = self.request.GET.get("estado", "")
        return contexto


class AlertaReintentarController(LoginRequiredMixin, View):
    def post(self, request, pk):
        alerta = get_object_or_404(AlertaTelegram, pk=pk)
        alerta = TelegramService().reintentar(alerta)
        if alerta.fue_exitosa:
            messages.success(request, "Alerta reenviada correctamente.")
        else:
            messages.error(request, f"No se pudo reenviar la alerta: {alerta.error}")
        destino = request.POST.get("siguiente") or "monitoreo:alerta_lista"
        if destino == "incidencia" and alerta.incidencia_id:
            return redirect("monitoreo:incidencia_detalle", pk=alerta.incidencia_id)
        return redirect("monitoreo:alerta_lista")
