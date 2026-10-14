from django import forms

from monitoreo.models import Dispositivo

from .base import FormularioBootstrapMixin


class DispositivoForm(FormularioBootstrapMixin, forms.ModelForm):
    class Meta:
        model = Dispositivo
        fields = ["vehiculo", "codigo", "mac_esp32", "mac_esp32cam", "ip", "version_firmware", "estado"]
        widgets = {
            "codigo": forms.TextInput(attrs={"placeholder": "NODO-01"}),
        }
