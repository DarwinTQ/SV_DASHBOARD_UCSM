from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from monitoreo.forms import TipoEventoForm
from monitoreo.models import TipoEvento

from ._base_crud import EliminarProtegidoMixin


class TipoEventoListController(LoginRequiredMixin, ListView):
    model = TipoEvento
    template_name = "monitoreo/tipos_evento/lista.html"
    context_object_name = "tipos_evento"
    paginate_by = 20


class TipoEventoCreateController(LoginRequiredMixin, CreateView):
    model = TipoEvento
    form_class = TipoEventoForm
    template_name = "monitoreo/tipos_evento/form.html"
    success_url = reverse_lazy("monitoreo:tipo_evento_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Tipo de evento creado correctamente.")
        return respuesta


class TipoEventoUpdateController(LoginRequiredMixin, UpdateView):
    model = TipoEvento
    form_class = TipoEventoForm
    template_name = "monitoreo/tipos_evento/form.html"
    success_url = reverse_lazy("monitoreo:tipo_evento_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Tipo de evento actualizado correctamente.")
        return respuesta


class TipoEventoDetalleController(LoginRequiredMixin, DetailView):
    model = TipoEvento
    template_name = "monitoreo/tipos_evento/detalle.html"
    context_object_name = "tipo_evento"


class TipoEventoDeleteController(EliminarProtegidoMixin, DeleteView):
    model = TipoEvento
    template_name = "monitoreo/tipos_evento/confirmar_borrar.html"
    success_url = reverse_lazy("monitoreo:tipo_evento_lista")
    mensaje_exito = "Tipo de evento eliminado correctamente."
