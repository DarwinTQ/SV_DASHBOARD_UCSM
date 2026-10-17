"""Comando: python manage.py sembrar_catalogos [--demo]

Siembra el catálogo fijo de tipos de evento (idempotente, update_or_create).
Con --demo además crea un conductor, un vehículo, el dispositivo NODO-01 de
ejemplo y el superusuario admin/admin123 para poder probar sin hardware.
"""
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from monitoreo.models import Conductor, Dispositivo, TipoEvento, Vehiculo


class SembradorCatalogos:
    """Encapsula la siembra de datos base del sistema."""

    # El nivel_riesgo NO es decorativo: es lo que decide la acción del servidor
    # (ver ReglasAccion en monitoreo/services/reglas_accion.py).
    #   ALTO  -> incidencia + alerta de Telegram a todos los autorizados
    #   MEDIO -> incidencia + alerta SILENCIOSA (disable_notification)
    #   BAJO  -> solo incidencia (registro, sin Telegram)
    CATALOGO_TIPOS_EVENTO = [
        {
            "codigo": "PERSONA",
            "nombre": "Persona detectada (YOLO)",
            "nivel_riesgo": "ALTO",
            "descripcion": "YOLO detectó una persona junto al vehículo",
        },
        # --- Reglas definitivas de la v3.0.0 (sección 3.4 del contrato MQTT) ---
        {
            "codigo": "CHOQUE",
            "nombre": "Choque detectado",
            "nivel_riesgo": "ALTO",
            "descripcion": (
                "En marcha: |a| ≥ 4 g confirmado con un golpe sonoro ≥ 112 dB SPL "
                "dentro de ±500 ms"
            ),
        },
        {
            "codigo": "ROTURA_VENTANA",
            "nombre": "Rotura de ventana",
            "nivel_riesgo": "ALTO",
            "descripcion": (
                "Estacionado y armado: golpe de 150–400 Hz seguido de un estallido "
                "de 3–7 kHz sostenido ≥ 150 ms, fusionado con la vibración del MPU"
            ),
        },
        {
            "codigo": "GOLPE_MANIPULACION",
            "nombre": "Golpe / manipulación",
            "nivel_riesgo": "MEDIO",
            "descripcion": (
                "Estacionado y armado: vibración > 0.15 g sin estallido de vidrio "
                "en 1 s (alerta silenciosa)"
            ),
        },
        {
            "codigo": "IMPACTO_SIN_CONFIRMAR",
            "nombre": "Impacto sin confirmar",
            "nivel_riesgo": "BAJO",
            "descripcion": "En marcha: |a| ≥ 4 g pero sin golpe sonoro en ±500 ms (solo registro)",
        },
        {
            "codigo": "VIDRIO_ROTO_EXTERNO",
            "nombre": "Vidrio roto externo",
            "nivel_riesgo": "BAJO",
            "descripcion": (
                "Estallido de vidrio sin vibración en el vehículo: se rompió algo "
                "cerca, no el auto (solo registro)"
            ),
        },
        # --- Reglas legado de la v2.0.0 (cfg::REGLAS_LEGADO = false) -----------
        {
            "codigo": "VIBRACION",
            "nombre": "Golpe o vibración",
            "nivel_riesgo": "MEDIO",
            "descripcion": "Impacto detectado por el MPU-6050",
        },
        {
            "codigo": "RUIDO",
            "nombre": "Ruido fuerte",
            "nivel_riesgo": "MEDIO",
            "descripcion": "Nivel sonoro sobre el umbral (INMP441)",
        },
        {
            "codigo": "MANIPULACION",
            "nombre": "Manipulación / inclinación",
            "nivel_riesgo": "ALTO",
            "descripcion": "Cambio de inclinación o giro brusco (gata, remolque)",
        },
        {
            "codigo": "MOVIMIENTO",
            "nombre": "Movimiento no autorizado",
            "nivel_riesgo": "ALTO",
            "descripcion": "El GPS reporta desplazamiento estando armado",
        },
    ]

    def __init__(self, stdout_write):
        self._escribir = stdout_write

    def sembrar_tipos_evento(self) -> None:
        for datos in self.CATALOGO_TIPOS_EVENTO:
            codigo = datos.pop("codigo")
            _, creado = TipoEvento.objects.update_or_create(codigo=codigo, defaults=datos)
            datos["codigo"] = codigo
            accion = "creado" if creado else "actualizado"
            self._escribir(f"  · Tipo de evento {codigo}: {accion}")

    def sembrar_datos_demo(self) -> None:
        conductor, creado = Conductor.objects.update_or_create(
            dni="71234567",
            defaults={
                "nombres": "Juan",
                "apellidos": "Pérez",
                "telefono": "+51987654321",
                "correo": "juan.perez@example.com",
                "direccion": "Arequipa, Perú",
            },
        )
        self._escribir(f"  · Conductor demo: {'creado' if creado else 'actualizado'} ({conductor})")

        vehiculo, creado = Vehiculo.objects.update_or_create(
            placa="V1A-234",
            defaults={
                "conductor": conductor,
                "marca": "Toyota",
                "modelo": "Yaris",
                "anio": 2020,
                "color": "Plata",
                "tipo": "SEDAN",
                "activo": True,
            },
        )
        self._escribir(f"  · Vehículo demo: {'creado' if creado else 'actualizado'} ({vehiculo})")

        dispositivo, creado = Dispositivo.objects.update_or_create(
            codigo="NODO-01",
            defaults={"vehiculo": vehiculo, "estado": "ACTIVO"},
        )
        self._escribir(f"  · Dispositivo demo: {'creado' if creado else 'actualizado'} ({dispositivo})")

        User = get_user_model()
        if not User.objects.filter(username="admin").exists():
            User.objects.create_superuser("admin", email="admin@ucsm.edu.pe", password="admin123")
            self._escribir("  · Superusuario 'admin' creado con contraseña 'admin123' — CÁMBIALA.")
        else:
            self._escribir("  · Superusuario 'admin' ya existía, no se modificó.")


class Command(BaseCommand):
    help = "Siembra el catálogo de tipos de evento (y datos demo con --demo)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--demo", action="store_true",
            help="Crea además conductor/vehículo/dispositivo NODO-01 de ejemplo y el superusuario admin.",
        )

    def handle(self, *args, **options):
        sembrador = SembradorCatalogos(self.stdout.write)
        self.stdout.write("Sembrando catálogo de tipos de evento…")
        sembrador.sembrar_tipos_evento()
        if options["demo"]:
            self.stdout.write("Sembrando datos de demostración…")
            sembrador.sembrar_datos_demo()
        self.stdout.write(self.style.SUCCESS("Siembra completada."))
