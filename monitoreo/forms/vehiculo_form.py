from django import forms

from monitoreo.models import Vehiculo

from .base import FormularioBootstrapMixin


class VehiculoForm(FormularioBootstrapMixin, forms.ModelForm):
    class Meta:
        model = Vehiculo
        fields = ["conductor", "placa", "marca", "modelo", "anio", "color", "tipo", "activo"]
        widgets = {
            "placa": forms.TextInput(attrs={"placeholder": "ABC-123"}),
        }
