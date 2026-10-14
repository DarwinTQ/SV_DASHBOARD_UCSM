from django import forms

from monitoreo.models import TipoEvento

from .base import FormularioBootstrapMixin


class TipoEventoForm(FormularioBootstrapMixin, forms.ModelForm):
    class Meta:
        model = TipoEvento
        fields = ["codigo", "nombre", "nivel_riesgo", "descripcion"]
        widgets = {"descripcion": forms.Textarea(attrs={"rows": 3})}
