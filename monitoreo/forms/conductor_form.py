from django import forms

from monitoreo.models import Conductor

from .base import FormularioBootstrapMixin


class ConductorForm(FormularioBootstrapMixin, forms.ModelForm):
    """``telegram_chat_id`` no se edita aquí: se sincroniza solo cuando el
    propietario se vincula con ``/vincular`` en el bot."""

    class Meta:
        model = Conductor
        fields = ["dni", "nombres", "apellidos", "telefono", "correo", "direccion"]
