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
            "monitoreo:configuracion", "monitoreo:mapa_vivo",
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


class MapaVivoTests(TestCase):
    """Página "Mapa en vivo" y sus endpoints de rastro (sección 5.6)."""

    def setUp(self):
        User = get_user_model()
        User.objects.create_user("probador", password="clave-segura-123")
        self.client.login(username="probador", password="clave-segura-123")

        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris",
            anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(
            vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO",
        )

        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        EstadoEnVivo._instancia = None
        self.estado_en_vivo = EstadoEnVivo.instancia()

    def tearDown(self):
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        EstadoEnVivo._instancia = None

    def test_la_pagina_elige_el_primer_dispositivo(self):
        resp = self.client.get(reverse("monitoreo:mapa_vivo"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["codigo_mapa"], "NODO-01")

    def test_codigo_desconocido_cae_al_primero(self):
        resp = self.client.get(reverse("monitoreo:mapa_vivo"), {"codigo": "NO-EXISTE"})
        self.assertEqual(resp.context["codigo_mapa"], "NODO-01")

    def test_api_rastro_devuelve_los_puntos_acumulados(self):
        for i in range(3):
            self.estado_en_vivo.actualizar_gps("NODO-01", {
                "fix": True, "lat": -16.3989123 + i * 1e-5, "lon": -71.5369871,
                "vel_kmh": 30.0, "rumbo_deg": 118.5, "sats": 8, "hdop": 0.9,
                "modo": "EN_MARCHA",
            })

        resp = self.client.get(reverse("api_panel_rastro", args=["NODO-01"]))
        datos = resp.json()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(datos["rastro"]), 3)
        self.assertEqual(datos["modo"], "EN_MARCHA")
        self.assertEqual(datos["ultimo"]["rumbo_deg"], 118.5)

    def test_api_limpiar_rastro_lo_vacia(self):
        self.estado_en_vivo.actualizar_gps("NODO-01", {
            "fix": True, "lat": -16.39, "lon": -71.53, "sats": 8, "modo": "EN_MARCHA",
        })
        resp = self.client.post(reverse("api_panel_rastro_limpiar", args=["NODO-01"]))

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(self.estado_en_vivo.rastro("NODO-01"), [])

    def test_estado_del_panel_incluye_modo_y_gps(self):
        self.estado_en_vivo.actualizar_modo("NODO-01", "ESTACIONADO", "CONTACTO")
        self.estado_en_vivo.actualizar_gps("NODO-01", {
            "fix": True, "lat": -16.39, "lon": -71.53, "sats": 8, "modo": "ESTACIONADO",
        })

        datos = self.client.get(reverse("api_panel_estado")).json()
        dispositivo = datos["dispositivos"][0]
        self.assertEqual(dispositivo["modo"], "ESTACIONADO")
        self.assertEqual(dispositivo["modo_fuente"], "CONTACTO")
        self.assertIsNotNone(dispositivo["gps"])


class ComandosMqttApiTests(TestCase):
    """Los endpoints que publican ``cmd`` y ``cam/cmd`` (secciones 5.3 y 5.5)."""

    def setUp(self):
        User = get_user_model()
        User.objects.create_user("probador", password="clave-segura-123")
        self.client.login(username="probador", password="clave-segura-123")

        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris",
            anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(
            vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO",
        )

        from monitoreo.services.mqtt_service import PublicadorConfiguracion
        from monitoreo.tests.test_puente_mqtt import ClienteMqttFalso

        PublicadorConfiguracion._instancia = None
        self.cliente = ClienteMqttFalso()
        PublicadorConfiguracion.instancia().registrar_cliente(self.cliente)

    def tearDown(self):
        from monitoreo.services.mqtt_service import PublicadorConfiguracion

        PublicadorConfiguracion._instancia = None

    def test_armar_publica_config_retenida(self):
        resp = self.client.post(
            reverse("api_panel_dispositivo_armar", args=[self.dispositivo.pk]),
            {"armado": False}, content_type="application/json",
        )
        datos = resp.json()

        self.assertTrue(datos["ok"])
        self.assertFalse(datos["armado"])
        self.assertTrue(datos["publicado_mqtt"])
        configs = self.cliente.publicadas("config")
        self.assertEqual(len(configs), 1)
        self.assertTrue(configs[0]["retener"])
        self.assertFalse(configs[0]["carga"]["armado"])

    def test_comando_recalibrar_piso(self):
        resp = self.client.post(
            reverse("api_panel_nodo_comando", args=[self.dispositivo.pk]),
            {"accion": "recalibrar_piso"}, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        comandos = self.cliente.publicadas("cmd")
        self.assertEqual(comandos[0]["carga"], {"accion": "recalibrar_piso"})

    def test_comando_forzar_modo_exige_un_modo_valido(self):
        resp = self.client.post(
            reverse("api_panel_nodo_comando", args=[self.dispositivo.pk]),
            {"accion": "forzar_modo", "modo": "VOLANDO"}, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.cliente.publicadas("cmd"), [])

    def test_comando_desconocido_se_rechaza(self):
        resp = self.client.post(
            reverse("api_panel_nodo_comando", args=[self.dispositivo.pk]),
            {"accion": "autodestruirse"}, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)

    def test_comando_de_camara_publica_cam_cmd(self):
        resp = self.client.post(
            reverse("api_panel_camara_comando", args=["NODO-01"]),
            {"resolucion": "SVGA", "calidad": 14, "fps_max": 6, "flash": True},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        comandos = self.cliente.publicadas("cam/cmd")
        self.assertEqual(comandos[0]["carga"], {
            "resolucion": "SVGA", "calidad": 14, "fps_max": 6, "flash": True,
        })

    def test_comando_de_camara_sin_parametros_se_rechaza(self):
        resp = self.client.post(
            reverse("api_panel_camara_comando", args=["NODO-01"]),
            {}, content_type="application/json",
        )
        self.assertEqual(resp.status_code, 400)
