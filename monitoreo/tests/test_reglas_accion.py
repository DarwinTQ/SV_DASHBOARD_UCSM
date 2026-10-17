"""Pruebas de la política de acción por ``nivel_riesgo`` (sección 5.4 y 6.2).

Lo que se verifica aquí es la tabla del contrato:

    CHOQUE / ROTURA_VENTANA      ALTO   -> incidencia + alerta con notificación
    GOLPE_MANIPULACION           MEDIO  -> incidencia + alerta SILENCIOSA
    IMPACTO_SIN_CONFIRMAR        BAJO   -> solo incidencia, sin Telegram
    VIDRIO_ROTO_EXTERNO          BAJO   -> solo incidencia, sin Telegram

Más la segunda regla de negocio del servidor: las incidencias PERSONA de la
vigilancia visual **solo** se generan con el vehículo ESTACIONADO y armado.
"""
from django.test import TestCase
from django.utils import timezone

from monitoreo.models import (
    AlertaTelegram, Conductor, Dispositivo, Incidencia, MedicionTiempo, TipoEvento, Vehiculo,
)
from monitoreo.services.detector_yolo import CajaDeteccion
from monitoreo.services.incidencia_service import IncidenciaService
from monitoreo.services.reglas_accion import DescriptorEvidencia, ReglasAccion
from monitoreo.tests.test_pipeline import DetectorYoloFalso, DifusorAlertasFalso, FabricaCamaraFalsa

EVIDENCIA_CHOQUE = {"a_pico_g": 6.8, "spl_pico_db": 115.2, "delta_ms": 42}
EVIDENCIA_ROTURA = {
    "e_baja_db": -38.2, "piso_baja_db": -55.0, "e_alta_db": -41.0, "piso_alta_db": -56.3,
    "planitud": 0.42, "duracion_ms": 208, "vib_g": 0.31, "delta_vib_ms": -40,
}

CATALOGO = [
    ("CHOQUE", "Choque detectado", "ALTO"),
    ("ROTURA_VENTANA", "Rotura de ventana", "ALTO"),
    ("GOLPE_MANIPULACION", "Golpe / manipulación", "MEDIO"),
    ("IMPACTO_SIN_CONFIRMAR", "Impacto sin confirmar", "BAJO"),
    ("VIDRIO_ROTO_EXTERNO", "Vidrio roto externo", "BAJO"),
    ("PERSONA", "Persona detectada (YOLO)", "ALTO"),
]


class ReglasAccionUnitariasTests(TestCase):
    """``ReglasAccion`` sin tocar el pipeline."""

    def test_riesgo_alto_alerta_con_notificacion(self):
        tipo = TipoEvento.objects.create(codigo="CHOQUE", nombre="Choque", nivel_riesgo="ALTO")
        accion = ReglasAccion.para(tipo)
        self.assertTrue(accion.alertar)
        self.assertFalse(accion.silenciosa)
        self.assertEqual(accion.nivel_catalogo, "ALERTA")

    def test_riesgo_medio_alerta_silenciosa(self):
        tipo = TipoEvento.objects.create(codigo="GOLPE_MANIPULACION", nombre="Golpe", nivel_riesgo="MEDIO")
        accion = ReglasAccion.para(tipo)
        self.assertTrue(accion.alertar)
        self.assertTrue(accion.silenciosa)
        self.assertEqual(accion.nivel_catalogo, "ALERTA_LEVE")

    def test_riesgo_bajo_solo_registro(self):
        tipo = TipoEvento.objects.create(codigo="VIDRIO_ROTO_EXTERNO", nombre="Vidrio", nivel_riesgo="BAJO")
        accion = ReglasAccion.para(tipo)
        self.assertFalse(accion.alertar)
        self.assertEqual(accion.nivel_catalogo, "REGISTRO")
        self.assertIn("solo registro", accion.descripcion)

    def test_nivel_del_firmware_coincidente_se_anota(self):
        tipo = TipoEvento.objects.create(codigo="CHOQUE", nombre="Choque", nivel_riesgo="ALTO")
        nota = ReglasAccion.validar_nivel_firmware(tipo, "ALERTA")
        self.assertIn("coincide con el catálogo", nota)

    def test_nivel_del_firmware_discrepante_se_denuncia(self):
        """Si el firmware y el catálogo no coinciden, manda el catálogo."""
        tipo = TipoEvento.objects.create(codigo="CHOQUE", nombre="Choque", nivel_riesgo="BAJO")
        nota = ReglasAccion.validar_nivel_firmware(tipo, "ALERTA")
        self.assertIn("manda el catálogo", nota)


class DescriptorEvidenciaTests(TestCase):
    def test_resumen_de_choque(self):
        self.assertEqual(
            DescriptorEvidencia.resumen_corto("CHOQUE", EVIDENCIA_CHOQUE),
            "6.8 g + 115.2 dB en 42 ms",
        )

    def test_resumen_de_rotura(self):
        resumen = DescriptorEvidencia.resumen_corto("ROTURA_VENTANA", EVIDENCIA_ROTURA)
        self.assertIn("estallido 208 ms", resumen)
        self.assertIn("planitud 0.42", resumen)
        self.assertIn("vibración 0.31 g", resumen)

    def test_resumen_de_impacto_sin_sonido(self):
        resumen = DescriptorEvidencia.resumen_corto(
            "IMPACTO_SIN_CONFIRMAR", {"a_pico_g": 4.6, "spl_pico_db": 0.0, "delta_ms": None},
        )
        self.assertIn("sin golpe sonoro", resumen)

    def test_texto_completo_incluye_el_piso_de_ruido(self):
        texto = DescriptorEvidencia.texto_completo("ROTURA_VENTANA", EVIDENCIA_ROTURA)
        self.assertIn("banda 150–400 Hz -38.2 dB", texto)
        self.assertIn("piso -55.0 dB", texto)
        self.assertIn("banda 3–7 kHz", texto)

    def test_sin_evidencia_no_inventa_texto(self):
        self.assertEqual(DescriptorEvidencia.texto_completo("CHOQUE", None), "")
        self.assertEqual(DescriptorEvidencia.resumen_corto("CHOQUE", {}), "")


class AccionPorTipoEnPipelineTests(TestCase):
    """Pipeline completo con dobles: comprueba la tabla de la sección 5.4."""

    def setUp(self):
        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
            telegram_chat_id="12345",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris",
            anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO")
        for codigo, nombre, riesgo in CATALOGO:
            TipoEvento.objects.create(codigo=codigo, nombre=nombre, nivel_riesgo=riesgo)

    def _servicio(self, difusor, cajas=None):
        return IncidenciaService(
            difusor_alertas=difusor,
            fabrica_camara=FabricaCamaraFalsa(exito=True),
            detector_yolo=DetectorYoloFalso(cajas=cajas or []),
        )

    def _evento(self, tipo: str, nivel: str, evidencia: dict, modo: str = "ESTACIONADO"):
        ahora_ms = int(timezone.now().timestamp() * 1000)
        return {
            "codigo": "NODO-01", "evento_uid": f"UID-{tipo}", "tipo": tipo, "nivel": nivel,
            "ts_ms": ahora_ms, "ts_fuente": "SERVIDOR", "modo": modo, "armado": True,
            "evidencia": evidencia, "origen": "MQTT",
            "aceleracion_g": evidencia.get("a_pico_g") or evidencia.get("vib_g") or 0,
            "nivel_audio_db": evidencia.get("spl_pico_db") or 0,
            "gps": {"fix": True, "sats": 8, "lat": -16.3989123, "lon": -71.5369871},
            "t_recepcion_ms": ahora_ms,
        }

    def _procesar(self, tipo, nivel, evidencia, modo="ESTACIONADO", cajas=None):
        difusor = DifusorAlertasFalso()
        incidencia = self._servicio(difusor, cajas).procesar_evento_sensor(
            self._evento(tipo, nivel, evidencia, modo),
        )
        return incidencia, difusor

    # ----------------------------- riesgo ALTO ----------------------------- #
    def test_choque_genera_alerta_con_notificacion(self):
        incidencia, difusor = self._procesar("CHOQUE", "ALERTA", EVIDENCIA_CHOQUE, modo="EN_MARCHA")

        self.assertEqual(incidencia.tipo.codigo, "CHOQUE")
        self.assertEqual(incidencia.estado, "NOTIFICADA")
        self.assertEqual(difusor.llamadas, [incidencia.pk])
        self.assertEqual(difusor.silenciosas, [False])
        self.assertEqual(difusor.evidencias, [EVIDENCIA_CHOQUE])
        # a_pico_g y spl_pico_db van a las columnas de la incidencia.
        self.assertAlmostEqual(incidencia.aceleracion_g, 6.8)
        self.assertAlmostEqual(incidencia.nivel_audio_db, 115.2)
        self.assertEqual(MedicionTiempo.objects.filter(incidencia=incidencia).count(), 1)

    def test_rotura_ventana_genera_alerta_y_guarda_la_evidencia(self):
        incidencia, difusor = self._procesar("ROTURA_VENTANA", "ALERTA", EVIDENCIA_ROTURA)

        self.assertEqual(difusor.silenciosas, [False])
        self.assertIn("estallido", incidencia.observacion.lower() + " ")
        self.assertIn("planitud 0.42", incidencia.observacion)
        self.assertIn("modo ESTACIONADO", incidencia.observacion)
        self.assertIn("vía MQTT", incidencia.observacion)

    # ----------------------------- riesgo MEDIO ---------------------------- #
    def test_golpe_manipulacion_genera_alerta_silenciosa(self):
        evidencia = {"vib_g": 0.42, "duracion_ms": 0, "planitud": 0.0}
        incidencia, difusor = self._procesar("GOLPE_MANIPULACION", "ALERTA_LEVE", evidencia)

        self.assertEqual(difusor.llamadas, [incidencia.pk])
        self.assertEqual(difusor.silenciosas, [True])
        self.assertEqual(incidencia.estado, "NOTIFICADA")

    # ----------------------------- riesgo BAJO ----------------------------- #
    def test_impacto_sin_confirmar_es_solo_registro(self):
        evidencia = {"a_pico_g": 4.6, "spl_pico_db": 0.0, "delta_ms": None}
        incidencia, difusor = self._procesar(
            "IMPACTO_SIN_CONFIRMAR", "REGISTRO", evidencia, modo="EN_MARCHA",
        )

        self.assertEqual(difusor.llamadas, [])
        self.assertEqual(AlertaTelegram.objects.filter(incidencia=incidencia).count(), 0)
        self.assertEqual(incidencia.estado, "PENDIENTE")
        # Aun sin alerta, la incidencia y su medición quedan registradas.
        self.assertEqual(Incidencia.objects.count(), 1)
        self.assertEqual(MedicionTiempo.objects.filter(incidencia=incidencia).count(), 1)

    def test_vidrio_roto_externo_es_solo_registro(self):
        evidencia = dict(EVIDENCIA_ROTURA, vib_g=0.0, delta_vib_ms=None)
        incidencia, difusor = self._procesar("VIDRIO_ROTO_EXTERNO", "REGISTRO", evidencia)

        self.assertEqual(difusor.llamadas, [])
        self.assertEqual(incidencia.estado, "PENDIENTE")

    # ------------------- interacción con YOLO y el catálogo ---------------- #
    def test_un_choque_no_se_reclasifica_a_persona(self):
        """El CHOQUE conserva su tipo y su evidencia aunque YOLO vea gente."""
        caja = CajaDeteccion(clase="person", confianza=0.9, x1=1, y1=1, x2=10, y2=20)
        incidencia, _ = self._procesar(
            "CHOQUE", "ALERTA", EVIDENCIA_CHOQUE, modo="EN_MARCHA", cajas=[caja],
        )
        self.assertEqual(incidencia.tipo.codigo, "CHOQUE")
        self.assertIn("YOLO además detectó", incidencia.observacion)

    def test_golpe_con_persona_se_reclasifica_a_persona(self):
        caja = CajaDeteccion(clase="person", confianza=0.9, x1=1, y1=1, x2=10, y2=20)
        evidencia = {"vib_g": 0.42, "duracion_ms": 0, "planitud": 0.0}
        incidencia, difusor = self._procesar(
            "GOLPE_MANIPULACION", "ALERTA_LEVE", evidencia, cajas=[caja],
        )
        self.assertEqual(incidencia.tipo.codigo, "PERSONA")
        self.assertIn("Disparado por sensor: GOLPE_MANIPULACION", incidencia.observacion)
        # PERSONA es riesgo ALTO: la alerta deja de ser silenciosa.
        self.assertEqual(difusor.silenciosas, [False])

    def test_cambiar_el_nivel_del_catalogo_cambia_la_accion(self):
        """Sin tocar el firmware: se baja CHOQUE a riesgo BAJO y deja de alertar."""
        TipoEvento.objects.filter(codigo="CHOQUE").update(nivel_riesgo="BAJO")
        incidencia, difusor = self._procesar("CHOQUE", "ALERTA", EVIDENCIA_CHOQUE, modo="EN_MARCHA")

        self.assertEqual(difusor.llamadas, [])
        self.assertIn("manda el catálogo", incidencia.observacion)


class PersonaSoloEstacionadoTests(TestCase):
    """``VigilanciaVisual`` no debe alertar PERSONA con el vehículo en marcha."""

    def setUp(self):
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        conductor = Conductor.objects.create(
            dni="71234567", nombres="Juan", apellidos="Pérez", telefono="+51987654321",
        )
        vehiculo = Vehiculo.objects.create(
            conductor=conductor, placa="v1a-234", marca="Toyota", modelo="Yaris",
            anio=2020, color="Plata",
        )
        self.dispositivo = Dispositivo.objects.create(vehiculo=vehiculo, codigo="NODO-01", estado="ACTIVO")
        TipoEvento.objects.create(codigo="PERSONA", nombre="Persona detectada (YOLO)", nivel_riesgo="ALTO")

        EstadoEnVivo._instancia = None
        self.estado_en_vivo = EstadoEnVivo.instancia()

    def tearDown(self):
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        EstadoEnVivo._instancia = None

    def _vigilancia(self):
        from monitoreo.workers.vigilancia_visual import VigilanciaVisual

        return VigilanciaVisual()

    def test_en_marcha_no_alerta(self):
        self.estado_en_vivo.actualizar_modo("NODO-01", "EN_MARCHA", "INERCIAL")
        self.assertFalse(self._vigilancia()._puede_alertar_persona(self.dispositivo))

    def test_estacionado_y_armado_si_alerta(self):
        self.estado_en_vivo.actualizar_modo("NODO-01", "ESTACIONADO", "INERCIAL")
        self.assertTrue(self._vigilancia()._puede_alertar_persona(self.dispositivo))

    def test_estacionado_pero_desarmado_no_alerta(self):
        self.estado_en_vivo.actualizar_modo("NODO-01", "ESTACIONADO", "INERCIAL")
        self.dispositivo.estado = "INACTIVO"
        self.assertFalse(self._vigilancia()._puede_alertar_persona(self.dispositivo))

    def test_sin_modo_conocido_no_alerta(self):
        """Mientras el nodo no publique 'modo' se es conservador."""
        self.assertFalse(self._vigilancia()._puede_alertar_persona(self.dispositivo))

    def test_confirmacion_se_descarta_si_el_vehiculo_esta_en_marcha(self):
        """Los frames confirmados se limpian: nada de alertas acumuladas."""
        from monitoreo.services.camara_service import ResultadoCaptura
        from monitoreo.services.detector_yolo import ResultadoDeteccion
        from monitoreo.workers.vigilancia_visual import FrameProcesado

        self.estado_en_vivo.actualizar_modo("NODO-01", "EN_MARCHA", "INERCIAL")
        vigilancia = self._vigilancia()
        caja = CajaDeteccion(clase="person", confianza=0.95, x1=1, y1=1, x2=10, y2=20)
        ahora = timezone.now()
        frame = FrameProcesado(
            ahora, ahora, ahora, ahora,
            ResultadoCaptura(exito=True, jpg=b"x", metodo="stream"),
            ResultadoDeteccion(cajas=[caja]),
        )

        for _ in range(5):
            vigilancia._evaluar_confirmacion_persona(self.dispositivo, frame)

        self.assertEqual(Incidencia.objects.count(), 0)
        self.assertEqual(vigilancia._frames_confirmados["NODO-01"], [])
