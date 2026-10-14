"""Mixin que aplica las clases de Bootstrap a todos los campos del formulario."""
from django import forms


class FormularioBootstrapMixin:
    """Agrega automáticamente ``form-control``/``form-select`` a cada widget."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for campo in self.fields.values():
            widget = campo.widget
            if isinstance(widget, (forms.Select, forms.SelectMultiple)):
                clase = "form-select"
            elif isinstance(widget, forms.CheckboxInput):
                clase = "form-check-input"
            else:
                clase = "form-control"
            existentes = widget.attrs.get("class", "")
            widget.attrs["class"] = f"{existentes} {clase}".strip()
