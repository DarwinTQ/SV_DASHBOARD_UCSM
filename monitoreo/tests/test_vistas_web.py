from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from monitoreo.models import Conductor, Dispositivo, TipoEvento, Vehiculo


class LoginRequeridoTests(TestCase):
    def test_panel_redirige_a_login_si_no_hay_sesion(self):
        resp = self.client.get(reverse("monitoreo:panel"))
        self.assertEqual(resp.status_code, 302)
        self.assertIn("/login/", resp.url)

    def test_incidencia_lista_redirige_a_login_si_no_hay_sesion(self):
        resp = self.client.get(reverse("monitoreo:incidencia_lista"))
        self.assertEqual(resp.status_code, 302)

    def test_login_valido_permite_acceder_al_panel(self):
        User = get_user_model()
        User.objects.create_user("probador", password="clave-segura-123")
        self.client.login(username="probador", password="clave-segura-123")
        resp = self.client.get(reverse("monitoreo:panel"))
        self.assertEqual(resp.status_code, 200)


class PaginasPrincipalesTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.usuario = User.objects.create_user("probador", password="clave-segura-123")
        self.client.login(username="probador", password="clave-segura-123")
        TipoEvento.objects.create(codigo="VIBRACION", nombre="Golpe o vibración", nivel_riesgo="MEDIO")

    def test_paginas_principales_responden_200(self):
        urls = [
            "monitoreo:panel", "monitoreo:incidencia_lista", "monitoreo:incidencia_mapa",
            "monitoreo:conductor_lista", "monitoreo:vehiculo_lista", "monitoreo:dispositivo_lista",
            "monitoreo:tipo_evento_lista", "monitoreo:alerta_lista", "monitoreo:resultados",
            "monitoreo:configuracion",
        ]
        for nombre in urls:
            with self.subTest(url=nombre):
                resp = self.client.get(reverse(nombre))
                self.assertEqual(resp.status_code, 200, f"{nombre} devolvió {resp.status_code}")


class CrudConductorTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_user("probador", password="clave-segura-123")
        self.client.login(username="probador", password="clave-segura-123")

    def test_crear_conductor(self):
        resp = self.client.post(reverse("monitoreo:conductor_crear"), {
            "dni": "71234567", "nombres": "Juan", "apellidos": "Pérez",
            "telefono": "+51987654321", "correo": "", "telegram_chat_id": "", "direccion": "",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Conductor.objects.filter(dni="71234567").exists())

    def test_editar_y_eliminar_conductor(self):
        conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")
        resp = self.client.post(reverse("monitoreo:conductor_editar", args=[conductor.pk]), {
            "dni": "71234567", "nombres": "Juan Carlos", "apellidos": "Pérez",
            "telefono": "+51987654321", "correo": "", "telegram_chat_id": "", "direccion": "",
        })
        self.assertEqual(resp.status_code, 302)
        conductor.refresh_from_db()
        self.assertEqual(conductor.nombres, "Juan Carlos")

        resp = self.client.post(reverse("monitoreo:conductor_eliminar", args=[conductor.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertFalse(Conductor.objects.filter(pk=conductor.pk).exists())


class CrudVehiculoYDispositivoTests(TestCase):
    def setUp(self):
        User = get_user_model()
        User.objects.create_user("probador", password="clave-segura-123")
        self.client.login(username="probador", password="clave-segura-123")
        self.conductor = Conductor.objects.create(dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321")

    def test_crear_vehiculo(self):
        resp = self.client.post(reverse("monitoreo:vehiculo_crear"), {
            "conductor": self.conductor.pk, "placa": "ABC-123", "marca": "Toyota", "modelo": "Yaris",
            "anio": 2020, "color": "Plata", "tipo": "SEDAN", "activo": "on",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Vehiculo.objects.filter(placa="ABC-123").exists())

    def test_eliminar_vehiculo_protegido_por_dispositivo(self):
        vehiculo = Vehiculo.objects.create(
            conductor=self.conductor, placa="ABC-123", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01")
        resp = self.client.post(reverse("monitoreo:vehiculo_eliminar", args=[vehiculo.pk]))
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Vehiculo.objects.filter(pk=vehiculo.pk).exists())

    def test_crear_dispositivo(self):
        vehiculo = Vehiculo.objects.create(
            conductor=self.conductor, placa="ABC-123", marca="Toyota", modelo="Yaris", anio=2020, color="Plata",
        )
        resp = self.client.post(reverse("monitoreo:dispositivo_crear"), {
            "vehiculo": vehiculo.pk, "codigo": "NODO-01", "mac_esp32": "", "mac_esp32cam": "",
            "ip": "", "version_firmware": "", "estado": "ACTIVO",
        })
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(Dispositivo.objects.filter(codigo="NODO-01").exists())
