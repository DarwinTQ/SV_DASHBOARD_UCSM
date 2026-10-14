from django.test import TestCase

from monitoreo.bot.limitador import LimitadorFrecuencia
from monitoreo.models import Conductor
from monitoreo.tests.dobles_bot import ClienteBotAPIFalso
from monitoreo.workers.bot_telegram import BotTelegramWorker


class PoolSincronoFalso:
    """Ejecuta ``submit`` en el mismo hilo, para que la prueba sea determinista."""

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)

    def shutdown(self, wait=True):
        pass


class ClienteConUpdatesFalso(ClienteBotAPIFalso):
    def __init__(self, updates_por_llamada):
        super().__init__()
        self._secuencia = list(updates_por_llamada)

    def get_updates(self, offset=None, timeout=None, allowed_updates=None):
        resultado = self._secuencia.pop(0) if self._secuencia else []
        return {"ok": True, "result": resultado, "_status_code": 200}


class BotTelegramWorkerTests(TestCase):
    def setUp(self):
        Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        LimitadorFrecuencia._instancia = None

    def test_ciclo_procesa_mensaje_y_avanza_offset(self):
        updates = [[{
            "update_id": 500,
            "message": {"message_id": 1, "chat": {"id": 111}, "text": "/ayuda", "from": {"first_name": "Juan"}},
        }]]
        worker = BotTelegramWorker()
        worker._cliente = ClienteConUpdatesFalso(updates)
        worker._pool = PoolSincronoFalso()

        worker.ciclo()

        self.assertEqual(worker._offset, 501)
        self.assertEqual(len(worker._cliente.mensajes_enviados), 1)

    def test_ciclo_procesa_callback_y_avanza_offset(self):
        updates = [[{
            "update_id": 700,
            "callback_query": {
                "id": "cbq1", "data": "ok:info", "from": {"first_name": "Juan"},
                "message": {"message_id": 9, "chat": {"id": 111}},
            },
        }]]
        worker = BotTelegramWorker()
        worker._cliente = ClienteConUpdatesFalso(updates)
        worker._pool = PoolSincronoFalso()

        worker.ciclo()

        self.assertEqual(worker._offset, 701)
        self.assertEqual(len(worker._cliente.callbacks_respondidos), 1)

    def test_ciclo_sin_updates_no_falla_y_no_mueve_offset(self):
        worker = BotTelegramWorker()
        worker._cliente = ClienteConUpdatesFalso([[]])
        worker._pool = PoolSincronoFalso()

        worker.ciclo()

        self.assertIsNone(worker._offset)

    def test_ciclo_con_fallo_de_red_no_lanza_excepcion(self):
        class ClienteFallido(ClienteBotAPIFalso):
            def get_updates(self, offset=None, timeout=None, allowed_updates=None):
                return {"ok": False, "description": "timeout", "_status_code": 0}

        worker = BotTelegramWorker()
        worker._cliente = ClienteFallido()
        worker._pool = PoolSincronoFalso()

        import time as _time

        inicio = _time.monotonic()
        worker.ciclo()
        self.assertLess(_time.monotonic() - inicio, 3)
