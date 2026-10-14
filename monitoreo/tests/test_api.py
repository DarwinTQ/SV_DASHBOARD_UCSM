from django.test import TestCase
from django.urls import reverse

from config import config
from monitoreo.models import Conductor, Dispositivo, EstadoDispositivo, TipoEvento, Vehiculo

API_KEY = config.esp32.api_key


def _telemetria_valida(codigo="NODO-01"):
    return {
        "codigo": codigo, "firmware": "2.0.0", "mac": "24:6F:28:AA:BB:CC", "ip": "10.144.55.50",
        "boot_id": 2882343476, "seq": 1, "uptime_ms": 6600, "ts_ms": 1791867074163,
        "ts_fuente": "SERVIDOR", "armado": True,
        "wifi": {"rssi_dbm": -58},
        "sistema": {"ram_libre_kb": 250, "temp_c": 33.1},
        "gps": {"fix": True, "sats": 7, "lat": -16.398912, "lon": -71.536987, "vel_kmh": 0.4, "alt_m": 2335.2, "hdop": 1.1},
        "imu": {"ax": 0, "ay": 0, "az": 1, "a_max_g": 1.05, "vib_rms_g": 0.0223, "vib_pp_g": 0.07,
                "giro_max_dps": 0, "pitch_deg": 0, "roll_deg": 0, "muestras": 200},
        "audio": {"rms_dbfs": -72.45, "spl_db": 47.55, "pico_dbfs": -48.93, "pico_spl_db": 71.07, "muestras": 16000},
        "bateria": {"v": 3.9, "pct": 65},
        "http": {"rtt_prev_ms": 12, "ok": 10, "fallos": 0, "descartados": 0},
    }


def _evento_valido(codigo="NODO-01", evento_uid="ABCD1234-1"):
    return {
        "codigo": codigo, "evento_uid": evento_uid, "tipo": "VIBRACION",
        "ts_ms": 1760330000113, "ts_fuente": "SERVIDOR",
        "valor": 0.82, "umbral": 0.5, "unidad": "g",
        "aceleracion_g": 1.31, "nivel_audio_db": 62.4,
        "gps": {"fix": True, "sats": 7, "lat": -16.398912, "lon": -71.536987, "vel_kmh": 0, "alt_m": 2335, "hdop": 1.1},
        "imu": {"pitch_deg": 0.4, "roll_deg": -1.2},
        "wifi": {"rssi_dbm": -58}, "bateria": {"v": 3.9, "pct": 65},
    }


class ApiComunTests(TestCase):
    def setUp(self):
        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO")
        TipoEvento.objects.create(codigo="VIBRACION", nombre="Golpe o vibración", nivel_riesgo="MEDIO")

    def test_telemetria_sin_api_key_devuelve_401(self):
        resp = self.client.post(
            reverse("api_telemetria"), data=_telemetria_valida(), content_type="application/json",
        )
        self.assertEqual(resp.status_code, 401)
        self.assertFalse(resp.json()["ok"])

    def test_telemetria_con_api_key_invalida_devuelve_401(self):
        resp = self.client.post(
            reverse("api_telemetria"), data=_telemetria_valida(), content_type="application/json",
            HTTP_X_API_KEY="clave-incorrecta",
        )
        self.assertEqual(resp.status_code, 401)

    def test_telemetria_dispositivo_inexistente_devuelve_404(self):
        resp = self.client.post(
            reverse("api_telemetria"), data=_telemetria_valida(codigo="NODO-99"),
            content_type="application/json", HTTP_X_API_KEY=API_KEY,
        )
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "dispositivo no registrado")

    def test_telemetria_json_invalido_devuelve_400(self):
        payload = _telemetria_valida()
        del payload["codigo"]
        resp = self.client.post(
            reverse("api_telemetria"), data=payload, content_type="application/json",
            HTTP_X_API_KEY=API_KEY,
        )
        self.assertEqual(resp.status_code, 400)
        self.assertFalse(resp.json()["ok"])

    def test_telemetria_valida_devuelve_200_y_actualiza_dispositivo(self):
        resp = self.client.post(
            reverse("api_telemetria"), data=_telemetria_valida(), content_type="application/json",
            HTTP_X_API_KEY=API_KEY,
        )
        self.assertEqual(resp.status_code, 200)
        cuerpo = resp.json()
        self.assertTrue(cuerpo["ok"])
        self.assertTrue(cuerpo["armado"])
        self.assertIn("umbrales", cuerpo)

        self.dispositivo.refresh_from_db()
        self.assertEqual(self.dispositivo.ip, "10.144.55.50")
        self.assertEqual(self.dispositivo.mac_esp32, "24:6F:28:AA:BB:CC")
        self.assertIsNotNone(self.dispositivo.ultimo_contacto)
        self.assertEqual(EstadoDispositivo.objects.count(), 1)

    def test_telemetria_respeta_intervalo_de_estado_dispositivo(self):
        for _ in range(3):
            self.client.post(
                reverse("api_telemetria"), data=_telemetria_valida(), content_type="application/json",
                HTTP_X_API_KEY=API_KEY,
            )
        self.assertEqual(EstadoDispositivo.objects.count(), 1)

    def test_evento_valido_devuelve_202_encolado(self):
        resp = self.client.post(
            reverse("api_eventos"), data=_evento_valido(), content_type="application/json",
            HTTP_X_API_KEY=API_KEY,
        )
        self.assertEqual(resp.status_code, 202)
        cuerpo = resp.json()
        self.assertTrue(cuerpo["ok"])
        self.assertTrue(cuerpo["encolado"])

    def test_evento_duplicado_devuelve_200(self):
        self.client.post(
            reverse("api_eventos"), data=_evento_valido(evento_uid="DUP-1"), content_type="application/json",
            HTTP_X_API_KEY=API_KEY,
        )
        resp = self.client.post(
            reverse("api_eventos"), data=_evento_valido(evento_uid="DUP-1"), content_type="application/json",
            HTTP_X_API_KEY=API_KEY,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["duplicado"])

    def test_evento_tipo_invalido_devuelve_400(self):
        payload = _evento_valido()
        payload["tipo"] = "INEXISTENTE"
        resp = self.client.post(
            reverse("api_eventos"), data=payload, content_type="application/json",
            HTTP_X_API_KEY=API_KEY,
        )
        self.assertEqual(resp.status_code, 400)
