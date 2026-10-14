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

    CATALOGO_TIPOS_EVENTO = [
        {
            "codigo": "PERSONA",
            "nombre": "Persona detectada (YOLO)",
            "nivel_riesgo": "ALTO",
            "descripcion": "YOLO detectó una persona junto al vehículo",
        },
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
