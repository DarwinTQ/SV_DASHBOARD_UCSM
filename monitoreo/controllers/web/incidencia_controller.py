import csv

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views.generic import DetailView, ListView, TemplateView

from monitoreo.forms import ValidacionIncidenciaForm
from monitoreo.models import Dispositivo, Incidencia, TipoEvento
from monitoreo.repositories.incidencia_repository import IncidenciaRepository


class IncidenciaListController(LoginRequiredMixin, ListView):
    template_name = "monitoreo/incidencias/lista.html"
    context_object_name = "incidencias"
    paginate_by = 20

    def _filtros(self) -> dict:
        parametros = self.request.GET
        return {
            "desde": parametros.get("desde") or None,
            "hasta": parametros.get("hasta") or None,
            "tipo": parametros.get("tipo") or None,
            "riesgo": parametros.get("riesgo") or None,
            "estado": parametros.get("estado") or None,
            "validacion": parametros.get("validacion") or None,
            "placa": parametros.get("placa") or None,
        }

    def get_queryset(self):
        return IncidenciaRepository().filtrar(**self._filtros())

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["filtros"] = self._filtros()
        contexto["tipos_evento"] = TipoEvento.objects.all()
        contexto["query_string"] = self.request.GET.urlencode()
        return contexto

    def get(self, request, *args, **kwargs):
        if request.GET.get("exportar") == "csv":
            return self._exportar_csv()
        return super().get(request, *args, **kwargs)

    def _exportar_csv(self) -> HttpResponse:
        incidencias = IncidenciaRepository().filtrar(**self._filtros())
        respuesta = HttpResponse(content_type="text/csv")
        respuesta["Content-Disposition"] = 'attachment; filename="incidencias.csv"'
        escritor = csv.writer(respuesta)
        escritor.writerow(["ID", "Fecha/hora", "Tipo", "Riesgo", "Placa", "Estado", "Validación"])
        for incidencia in incidencias:
            escritor.writerow([
                incidencia.pk, incidencia.fecha_hora, incidencia.tipo.nombre, incidencia.tipo.nivel_riesgo,
                incidencia.dispositivo.vehiculo.placa, incidencia.estado, incidencia.validacion or "",
            ])
        return respuesta


class IncidenciaDetalleController(LoginRequiredMixin, DetailView):
    model = Incidencia
    template_name = "monitoreo/incidencias/detalle.html"
    context_object_name = "incidencia"

    def get_queryset(self):
        return IncidenciaRepository().base_queryset()

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["formulario"] = ValidacionIncidenciaForm(instance=self.object)
        contexto["detecciones"] = self.object.detecciones.all()
        contexto["alertas"] = self.object.alertas.select_related("conductor").all()
        contexto["medicion"] = getattr(self.object, "tiempos", None)
        return contexto

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        formulario = ValidacionIncidenciaForm(request.POST, instance=self.object)
        if formulario.is_valid():
            formulario.save()
            messages.success(request, "Incidencia actualizada correctamente.")
        else:
            messages.error(request, "No se pudo guardar: revisa los campos del formulario.")
        return redirect("monitoreo:incidencia_detalle", pk=self.object.pk)


class IncidenciaMapaController(LoginRequiredMixin, TemplateView):
    template_name = "monitoreo/incidencias/mapa.html"

    def get_context_data(self, **kwargs):
        contexto = super().get_context_data(**kwargs)
        contexto["tipos_evento"] = TipoEvento.objects.all()
        return contexto
