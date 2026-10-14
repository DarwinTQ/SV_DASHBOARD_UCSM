"""Mixins compartidos por los controladores CRUD (conductor/vehículo/etc.)."""
from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import ProtectedError
from django.shortcuts import redirect


class EliminarProtegidoMixin(LoginRequiredMixin):
    """Si el registro tiene relaciones protegidas, muestra un mensaje amable
    en lugar de que Django lance un error 500."""

    mensaje_exito = "Registro eliminado correctamente."

    def post(self, request, *args, **kwargs):
        self.object = self.get_object()
        try:
            self.object.delete()
            messages.success(request, self.mensaje_exito)
        except ProtectedError:
            messages.error(
                request,
                "No se puede eliminar: este registro tiene información asociada "
                "(incidencias, vehículos u otros registros dependientes).",
            )
        return redirect(self.success_url)
