"""JSON para el frontend autenticado por sesión (sección 10)."""
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from monitoreo.models import Dispositivo
from monitoreo.repositories.dispositivo_repository import DispositivoRepository
from monitoreo.repositories.incidencia_repository import IncidenciaRepository
from monitoreo.services.estado_en_vivo import EstadoEnVivo


class PanelApiBase(APIView):
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]


class PanelEstadoApi(PanelApiBase):
    """Estado en vivo de todos los dispositivos (fallback de sondeo si el WS no conecta)."""

    def get(self, request):
        estado_en_vivo = EstadoEnVivo.instancia()
        repo = DispositivoRepository()
        resultado = []
        for dispositivo in repo.todos():
            lectura = estado_en_vivo.obtener(dispositivo.codigo)
            # El tópico MQTT 'estado' (y su Last Will) manda sobre el timeout.
            en_linea = (
                lectura.en_linea_mqtt if lectura and lectura.en_linea_mqtt is not None
                else repo.en_linea(dispositivo)
            )
            ultimo_gps = estado_en_vivo.ultimo_punto_gps(dispositivo.codigo)
            resultado.append({
                "codigo": dispositivo.codigo, "placa": dispositivo.vehiculo.placa,
                "armado": dispositivo.armado, "en_linea": en_linea,
                "ultimo_contacto": dispositivo.ultimo_contacto.isoformat() if dispositivo.ultimo_contacto else None,
                "ultima_telemetria": lectura.ultima_telemetria if lectura else None,
                "camara_estado": lectura.camara_estado if lectura else None,
                "ultimo_frame_url": lectura.ultimo_frame_url if lectura else None,
                "fps_vision": lectura.fps_vision if lectura else None,
                "personas_detectadas": lectura.personas_detectadas if lectura else 0,
                # v3.0.0: modo del vehículo y última posición, para el mapa en vivo.
                "modo": lectura.modo if lectura else "",
                "modo_fuente": lectura.modo_fuente if lectura else "",
                "contacto": lectura.contacto if lectura else None,
                "gps": ultimo_gps.a_dict() if ultimo_gps else None,
            })
        return Response({"ok": True, "dispositivos": resultado})


class PanelIncidenciasRecientesApi(PanelApiBase):
    """Últimas incidencias para el feed del panel; con filtros opcionales
    (desde/hasta/tipo) también alimenta el mapa de incidencias."""

    def get(self, request):
        parametros = request.query_params
        repo = IncidenciaRepository()
        if parametros.get("desde") or parametros.get("hasta") or parametros.get("tipo"):
            qs = repo.filtrar(
                desde=parametros.get("desde") or None, hasta=parametros.get("hasta") or None,
                tipo=parametros.get("tipo") or None,
            ).order_by("-fecha_hora")[:200]
        else:
            qs = repo.recientes(10)

        datos = [{
            "id": inc.pk, "tipo": inc.tipo.codigo, "nombre_tipo": inc.tipo.nombre,
            "riesgo": inc.tipo.nivel_riesgo, "placa": inc.dispositivo.vehiculo.placa,
            "fecha_hora": inc.fecha_hora.isoformat(), "estado": inc.estado,
            "miniatura": (inc.imagen_anotada.url if inc.imagen_anotada else (inc.imagen.url if inc.imagen else None)),
            "lat": float(inc.latitud) if inc.latitud is not None else None,
            "lon": float(inc.longitud) if inc.longitud is not None else None,
        } for inc in qs]
        return Response({"ok": True, "incidencias": datos})


class PanelMetricasApi(PanelApiBase):
    """Estadísticas completas (sección 8.6), usadas por la página Resultados."""

    def get(self, request):
        from monitoreo.services.metricas_service import MetricasService

        parametros = request.query_params
        resultados = MetricasService().resultados_completos(
            desde=parametros.get("desde") or None, hasta=parametros.get("hasta") or None,
            dispositivo_id=parametros.get("dispositivo") or None, tipo=parametros.get("tipo") or None,
        )
        return Response({"ok": True, **resultados})


class DispositivoArmarApi(PanelApiBase):
    """Interruptor Armar/Desarmar del panel. El cambio viaja al nodo por el
    tópico MQTT ``config`` retenido (ver ``ControlArmadoService``)."""

    def post(self, request, pk):
        from monitoreo.services.control_armado_service import ControlArmadoService

        try:
            dispositivo = Dispositivo.objects.get(pk=pk)
        except Dispositivo.DoesNotExist:
            return Response({"ok": False, "error": "dispositivo no encontrado"}, status=404)

        resultado = ControlArmadoService().aplicar(dispositivo, bool(request.data.get("armado")))
        return Response({
            "ok": True, "armado": resultado.armado,
            "publicado_mqtt": resultado.publicado_mqtt, "mensaje": resultado.mensaje,
        })


class PanelRastroApi(PanelApiBase):
    """Rastro GPS en memoria para el mapa en vivo (sección 5.6).

    Lo usa la página al cargar, antes de que empiecen a llegar puntos por
    WebSocket, para que el mapa no aparezca vacío.
    """

    def get(self, request, codigo):
        estado_en_vivo = EstadoEnVivo.instancia()
        segundos = float(request.query_params.get("segundos") or 300)
        ultimo = estado_en_vivo.ultimo_punto_gps(codigo)
        return Response({
            "ok": True, "codigo": codigo,
            "modo": estado_en_vivo.modo(codigo),
            "ultimo": ultimo.a_dict() if ultimo else None,
            "rastro": estado_en_vivo.rastro(codigo, segundos),
        })


class PanelRastroLimpiarApi(PanelApiBase):
    """Botón "Limpiar rastro" del mapa."""

    def post(self, request, codigo):
        EstadoEnVivo.instancia().limpiar_rastro(codigo)
        return Response({"ok": True, "codigo": codigo})


class CamaraComandoApi(PanelApiBase):
    """Publica ``cam/cmd`` desde la página Configuración (sección 5.5)."""

    CAMPOS = ("resolucion", "calidad", "fps_max", "flash", "reiniciar")

    def post(self, request, codigo):
        from monitoreo.services.mqtt_service import PublicadorConfiguracion

        opciones = {}
        for campo in self.CAMPOS:
            if campo not in request.data:
                continue
            valor = request.data[campo]
            if campo in ("calidad", "fps_max"):
                try:
                    opciones[campo] = int(valor)
                except (TypeError, ValueError):
                    return Response({"ok": False, "error": f"{campo} debe ser un número entero"}, status=400)
            elif campo in ("flash", "reiniciar"):
                opciones[campo] = str(valor).lower() in ("1", "true", "on", "si", "sí")
            else:
                opciones[campo] = str(valor)

        if not opciones:
            return Response(
                {"ok": False, "error": f"indica al menos uno de: {', '.join(self.CAMPOS)}"}, status=400,
            )

        enviado = PublicadorConfiguracion.instancia().enviar_comando_camara(codigo, **opciones)
        if not enviado:
            return Response({
                "ok": False,
                "error": "sin conexión al broker MQTT; no se pudo enviar el comando a la cámara",
            }, status=503)
        return Response({"ok": True, "codigo": codigo, "enviado": opciones})


class NodoComandoApi(PanelApiBase):
    """Publica ``cmd`` al nodo: reiniciar, recalibrar_piso, forzar_modo."""

    ACCIONES = ("reiniciar", "recalibrar_piso", "forzar_modo")

    def post(self, request, pk):
        from monitoreo.services.mqtt_service import PublicadorConfiguracion

        try:
            dispositivo = Dispositivo.objects.get(pk=pk)
        except Dispositivo.DoesNotExist:
            return Response({"ok": False, "error": "dispositivo no encontrado"}, status=404)

        accion = str(request.data.get("accion") or "")
        if accion not in self.ACCIONES:
            return Response(
                {"ok": False, "error": f"acción inválida; usa una de: {', '.join(self.ACCIONES)}"}, status=400,
            )

        extra = {}
        if accion == "forzar_modo":
            modo = str(request.data.get("modo") or "")
            if modo not in ("EN_MARCHA", "ESTACIONADO"):
                return Response(
                    {"ok": False, "error": "modo inválido; usa EN_MARCHA o ESTACIONADO"}, status=400,
                )
            extra["modo"] = modo

        enviado = PublicadorConfiguracion.instancia().enviar_comando(dispositivo, accion, **extra)
        if not enviado:
            return Response(
                {"ok": False, "error": "sin conexión al broker MQTT; no se pudo enviar el comando"}, status=503,
            )
        return Response({"ok": True, "codigo": dispositivo.codigo, "accion": accion, **extra})
