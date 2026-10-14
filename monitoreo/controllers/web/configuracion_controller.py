import time

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views.generic import TemplateView, View

from config import config
from monitoreo.services.camara_service import FabricaCamara
from monitoreo.services.detector_yolo import DetectorYOLO
from monitoreo.services.telegram_service import TelegramService


class ConfiguracionController(LoginRequiredMixin, TemplateView):
    template_name = "monitoreo/configuracion/configuracion.html"

    def get_context_data(self, **kwargs):
        from monitoreo.bot.estado_bot import EstadoBot

        contexto = super().get_context_data(**kwargs)
        contexto["resumen"] = config.resumen_seguro()
        contexto["umbrales"] = config.umbrales.a_dict()
        contexto["estado_bot"] = EstadoBot.instancia().resumen()
        return contexto


class ProbarTelegramController(LoginRequiredMixin, View):
    def post(self, request):
        chat_id = request.POST.get("chat_id", "").strip()
        if not chat_id:
            return JsonResponse({"ok": False, "error": "Debes indicar un chat_id."}, status=400)
        try:
            resultado = TelegramService().enviar_prueba(chat_id)
            return JsonResponse({"ok": resultado["status"] < 300, **resultado})
        except Exception as exc:
            return JsonResponse({"ok": False, "error": str(exc)}, status=502)


class ProbarCamaraController(LoginRequiredMixin, View):
    def post(self, request):
        codigo = request.POST.get("codigo", "").strip()
        resultado = FabricaCamara.para(codigo).capturar()
        if not resultado.exito:
            return JsonResponse({"ok": False, "error": resultado.error}, status=502)

        import base64

        return JsonResponse({
            "ok": True, "captura_ms": resultado.captura_ms,
            "imagen_base64": base64.b64encode(resultado.jpg).decode("ascii"),
        })


class ProbarYoloController(LoginRequiredMixin, View):
    def post(self, request):
        archivo = request.FILES.get("imagen")
        if not archivo:
            return JsonResponse({"ok": False, "error": "Debes subir una imagen."}, status=400)

        inicio = time.monotonic()
        resultado = DetectorYOLO.instancia().detectar(archivo.read())
        tiempo_total_ms = round((time.monotonic() - inicio) * 1000, 2)

        import base64

        return JsonResponse({
            "ok": True,
            "tiempo_inferencia_ms": resultado.tiempo_inferencia_ms,
            "tiempo_total_ms": tiempo_total_ms,
            "num_detecciones": len(resultado.cajas),
            "hay_persona": resultado.hay_persona,
            "imagen_anotada_base64": (
                base64.b64encode(resultado.imagen_anotada_jpg).decode("ascii")
                if resultado.imagen_anotada_jpg else None
            ),
        })
