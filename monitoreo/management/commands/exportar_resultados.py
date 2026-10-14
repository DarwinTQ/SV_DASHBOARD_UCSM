"""Comando: python manage.py exportar_resultados [--desde AAAA-MM-DD] [--hasta AAAA-MM-DD]
                                                   [--dispositivo <codigo>] [--salida archivo.xlsx]

Genera el libro de Excel de resultados (sección 8.6) sin pasar por la web,
útil para adjuntar directamente al Capítulo V de la tesis.
"""
from pathlib import Path

from django.core.management.base import BaseCommand

from config import config
from monitoreo.models import Dispositivo
from monitoreo.services.exportacion_service import ExportacionService


class Command(BaseCommand):
    help = "Exporta a Excel las estadísticas de resultados del sistema."

    def add_arguments(self, parser):
        parser.add_argument("--desde", default=None, help="Fecha/hora inicial (AAAA-MM-DD).")
        parser.add_argument("--hasta", default=None, help="Fecha/hora final (AAAA-MM-DD).")
        parser.add_argument("--dispositivo", default=None, help="Código del dispositivo (ej. NODO-01).")
        parser.add_argument("--salida", default=None, help="Ruta del archivo .xlsx a generar.")

    def handle(self, *args, **options):
        dispositivo_id = None
        if options["dispositivo"]:
            dispositivo = Dispositivo.objects.filter(codigo=options["dispositivo"]).first()
            if dispositivo is None:
                self.stderr.write(self.style.ERROR(f"No existe el dispositivo '{options['dispositivo']}'."))
                return
            dispositivo_id = dispositivo.pk

        contenido = ExportacionService().generar_excel(
            desde=options["desde"], hasta=options["hasta"], dispositivo_id=dispositivo_id,
        )

        salida = Path(options["salida"]) if options["salida"] else config.BASE_DIR / "resultados_monitoreo_ucsm.xlsx"
        salida.write_bytes(contenido)
        self.stdout.write(self.style.SUCCESS(f"Excel generado en: {salida}"))
