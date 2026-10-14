from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Q
from django.urls import reverse_lazy
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from monitoreo.forms import VehiculoForm
from monitoreo.models import Vehiculo

from ._base_crud import EliminarProtegidoMixin


class VehiculoListController(LoginRequiredMixin, ListView):
    model = Vehiculo
    template_name = "monitoreo/vehiculos/lista.html"
    context_object_name = "vehiculos"
    paginate_by = 20

    def get_queryset(self):
        qs = super().get_queryset().select_related("conductor")
        busqueda = self.request.GET.get("q")
        if busqueda:
            qs = qs.filter(Q(placa__icontains=busqueda) | Q(marca__icontains=busqueda) | Q(modelo__icontains=busqueda))
        return qs

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["busqueda"] = self.request.GET.get("q", "")
        return contexto


class VehiculoCreateController(LoginRequiredMixin, CreateView):
    model = Vehiculo
    form_class = VehiculoForm
    template_name = "monitoreo/vehiculos/form.html"
    success_url = reverse_lazy("monitoreo:vehiculo_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Vehículo creado correctamente.")
        return respuesta


class VehiculoUpdateController(LoginRequiredMixin, UpdateView):
    model = Vehiculo
    form_class = VehiculoForm
    template_name = "monitoreo/vehiculos/form.html"
    success_url = reverse_lazy("monitoreo:vehiculo_lista")

    def form_valid(self, form):
        respuesta = super().form_valid(form)
        messages.success(self.request, "Vehículo actualizado correctamente.")
        return respuesta


class VehiculoDetalleController(LoginRequiredMixin, DetailView):
    model = Vehiculo
    template_name = "monitoreo/vehiculos/detalle.html"
    context_object_name = "vehiculo"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["accesos_telegram"] = self.object.accesos_telegram.filter(activo=True).order_by("-rol", "nombres")
        return contexto


class VehiculoDeleteController(EliminarProtegidoMixin, DeleteView):
    model = Vehiculo
    template_name = "monitoreo/vehiculos/confirmar_borrar.html"
    success_url = reverse_lazy("monitoreo:vehiculo_lista")
    mensaje_exito = "Vehículo eliminado correctamente."
