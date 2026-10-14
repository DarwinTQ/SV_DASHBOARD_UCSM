"""Sirve /media/ únicamente a usuarios autenticados (sección 12)."""
from pathlib import Path

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import FileResponse, Http404
from django.views.generic import View

from config import config


class MediaController(LoginRequiredMixin, View):
    def get(self, request, ruta: str):
        base = config.bd.media_root.resolve()
        destino = (base / ruta).resolve()
        if base not in destino.parents and destino != base:
            raise Http404("Ruta inválida.")
        if not destino.exists() or not destino.is_file():
            raise Http404("Archivo no encontrado.")
        return FileResponse(open(destino, "rb"))
