from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from monitoreo.forms import ConductorForm
from monitoreo.models import Conductor

from ._base_crud import EliminarProtegidoMixin


class ConductorListController(LoginRequiredMixin, ListView):
    model = Conductor
    template_name = "monitoreo/conductores/lista.html"
    context_object_name = "conductores"
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset()
        busqueda = self.request.GET.get("q")
        if busqueda:
            qs = qs.filter(Q(dni__icontains=busqueda) | Q(nombres__icontains=busqueda) | Q(apellidos__icontains=busqueda))
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busqueda"] = self.request.GET.get("q", "")
        return contexto


class ConductorCreateController(LoginRequiredMixin, CreateView):
    model = Conductor
    form_class = ConductorForm
    template_name = "monitoreo/conductores/form.html"
    success_url = reverse_lazy("monitoreo:conductor_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Conductor creado correctamente.")
        return respuesta


class ConductorUpdateController(LoginRequiredMixin, UpdateView):
    model = Conductor
    form_class = ConductorForm
    template_name = "monitoreo/conductores/form.html"
    success_url = reverse_lazy("monitoreo:conductor_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Conductor actualizado correctamente.")
        return respuesta


class ConductorDetalleController(LoginRequiredMixin, DetailView):
    model = Conductor
    template_name = "monitoreo/conductores/detalle.html"
    context_object_name = "conductor"


class ConductorDeleteController(EliminarProtegidoMixin, DeleteView):
    model = Conductor
    template_name = "monitoreo/conductores/confirmar_borrar.html"
    success_url = reverse_lazy("monitoreo:conductor_lista")
    mensaje_exito = "Conductor eliminado correctamente."
