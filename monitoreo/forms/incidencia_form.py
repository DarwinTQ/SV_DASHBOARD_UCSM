from django import forms

from monitoreo.models import Incidencia

from .base import FormularioBootstrapMixin


class ValidacionIncidenciaForm(FormularioBootstrapMixin, forms.ModelForm):
    class Meta:
        model = Incidencia
        fields = ["estado", "validacion", "observacion"]
        widgets = {"observacion": forms.Textarea(attrs={"rows": 3})}
