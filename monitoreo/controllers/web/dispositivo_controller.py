from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView, View

from config import config
from monitoreo.forms import DispositivoForm
from monitoreo.models import Dispositivo
from monitoreo.repositories.dispositivo_repository import DispositivoRepository
from monitoreo.services.camara_service import FabricaCamara
from monitoreo.services.control_armado_service import ControlArmadoService

from ._base_crud import EliminarProtegidoMixin


class DispositivoListController(LoginRequiredMixin, ListView):
    model = Dispositivo
    template_name = "monitoreo/dispositivos/lista.html"
    context_object_name = "dispositivos"
    paginate_by = 20

    def get_queryset(self):
        return DispositivoRepository().todos()


class DispositivoCreateController(LoginRequiredMixin, CreateView):
    model = Dispositivo
    form_class = DispositivoForm
    template_name = "monitoreo/dispositivos/form.html"
    success_url = reverse_lazy("monitoreo:dispositivo_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Dispositivo creado correctamente.")
        return respuesta


class DispositivoUpdateController(LoginRequiredMixin, UpdateView):
    model = Dispositivo
    form_class = DispositivoForm
    template_name = "monitoreo/dispositivos/form.html"
    success_url = reverse_lazy("monitoreo:dispositivo_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Dispositivo actualizado correctamente.")
        return respuesta


class DispositivoDetalleController(LoginRequiredMixin, DetailView):
    model = Dispositivo
    template_name = "monitoreo/dispositivos/detalle.html"
    context_object_name = "dispositivo"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        repo = DispositivoRepository()
        contexto["en_linea"] = repo.en_linea(self.object)
        contexto["historial_estados"] = repo.historial_estados(self.object)
        contexto["disponibilidad_pct"] = repo.disponibilidad_pct(self.object)
        contexto["url_camara"] = config.url_camara(self.object.codigo)
        contexto["url_stream_camara"] = config.url_stream_camara(self.object.codigo)
        return contexto


class DispositivoDeleteController(EliminarProtegidoMixin, DeleteView):
    model = Dispositivo
    template_name = "monitoreo/dispositivos/confirmar_borrar.html"
    success_url = reverse_lazy("monitoreo:dispositivo_lista")
    mensaje_exito = "Dispositivo eliminado correctamente."


class DispositivoArmarController(LoginRequiredMixin, View):
    """POST /dispositivos/<id>/armar/ — botón Armar/Desarmar desde la web o el topbar.

    El cambio llega al nodo por el tópico MQTT ``config`` (retenido), que es lo
    que hace ``ControlArmadoService``.
    """

    def post(self, request, pk):
        dispositivo = get_object_or_404(Dispositivo, pk=pk)
        armar = request.POST.get("armado") == "1"
        resultado = ControlArmadoService().aplicar(dispositivo, armar)

        if request.headers.get("x-requested-with") == "XMLHttpRequest":
            return JsonResponse({
                "ok": True, "armado": resultado.armado,
                "publicado_mqtt": resultado.publicado_mqtt, "mensaje": resultado.mensaje,
            })
        messages.success(request, resultado.mensaje)
        return redirect("monitoreo:dispositivo_detalle", pk=pk)


class DispositivoProbarCamaraController(LoginRequiredMixin, View):
    """POST /dispositivos/<id>/probar-camara/ — toma una foto de prueba."""

    def post(self, request, pk):
        dispositivo = get_object_or_404(Dispositivo, pk=pk)
        resultado = FabricaCamara.para(dispositivo.codigo).capturar()
        if not resultado.exito:
            return JsonResponse({"ok": False, "error": resultado.error}, status=502)

        import base64

        return JsonResponse({
            "ok": True, "captura_ms": resultado.captura_ms,
            "imagen_base64": base64.b64encode(resultado.jpg).decode("ascii"),
        })
