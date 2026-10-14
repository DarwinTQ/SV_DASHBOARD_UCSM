from django.apps import AppConfig


class MonitoreoConfig(AppConfig):
    """Configuración de la app. No arranca workers aquí (eso lo hace main.py)."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "monitoreo"
    verbose_name = "Monitoreo vehicular"

    def ready(self) -> None:
        self._activar_concurrencia_sqlite()

    @staticmethod
    def _activar_concurrencia_sqlite() -> None:
        """PRAGMAs para que SQLite tolere varios hilos escribiendo a la vez
        (workers de main.py, el pool del bot, y los hilos de prueba). Se
        registra aquí (no solo en main.py) para que también aplique bajo
        ``manage.py test``/``shell``, no solo en el servidor real."""
        from django.db.backends.signals import connection_created

        def _activar(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                cursor = connection.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("PRAGMA busy_timeout=5000;")

        connection_created.connect(_activar, dispatch_uid="activar_concurrencia_sqlite")
