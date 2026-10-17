"""Pruebas del modelo propio de la tesis (``yolo26_ucsm_best.pt``).

Sus clases son ``abriendo_puerta``, ``arma_blanca``, ``arma_fuego``,
``car broken`` y ``suspicious`` — **no** incluye ``person``. Lo que se verifica
aquí es que cada una de esas clases llega hasta la alerta de Telegram:

    clase YOLO         ->  TipoEvento         ->  acción
    arma_fuego             ARMA_FUEGO             alerta (ALTO)
    arma_blanca            ARMA_BLANCA            alerta (ALTO)
    abriendo_puerta        APERTURA_PUERTA        alerta (ALTO)
    car broken             VEHICULO_DANADO        alerta (ALTO)
    suspicious             PERSONA_SOSPECHOSA     alerta (ALTO)

Antes de esto todo el camino visual comparaba contra la clase ``"person"``, que
el modelo nunca emite: no se creaba incidencia y por tanto nunca salía la
alerta. Estas pruebas son la red para que no vuelva a pasar.
"""
from django.test import TestCase
from django.utils import timezone

from config import config
from monitoreo.management.commands.sembrar_catalogos import SembradorCatalogos
from monitoreo.models import AlertaTelegram, Conductor, Dispositivo, Incidencia, TipoEvento, Vehiculo
from monitoreo.services.camara_service import ResultadoCaptura
from monitoreo.services.detector_yolo import CajaDeteccion, ResultadoDeteccion
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.services.incidencia_service import IncidenciaService
from monitoreo.tests.test_pipeline import DifusorAlertasFalso, FabricaCamaraFalsa

CLASES_DEL_MODELO = ["abriendo_puerta", "arma_blanca", "arma_fuego", "car broken", "suspicious"]

TIPOS_ESPERADOS = {
    "abriendo_puerta": "APERTURA_PUERTA",
    "arma_blanca": "ARMA_BLANCA",
    "arma_fuego": "ARMA_FUEGO",
    "car broken": "VEHICULO_DANADO",
    "suspicious": "PERSONA_SOSPECHOSA",
}


def _caja(clase: str, confianza: float = 0.9) -> CajaDeteccion:
    return CajaDeteccion(clase=clase, confianza=confianza, x1=1, y1=1, x2=50, y2=80)


class ClasesVigiladasConfigTests(TestCase):
    """La correspondencia clase -> TipoEvento vive en ``config.yolo``."""

    def test_las_cinco_clases_del_modelo_estan_vigiladas(self):
        for clase in CLASES_DEL_MODELO:
            with self.subTest(clase=clase):
                vigilada = config.yolo.vigilada(clase)
                self.assertIsNotNone(vigilada, f"la clase {clase!r} no está en CATALOGO_CLASES_YOLO")
                self.assertEqual(vigilada.tipo_evento, TIPOS_ESPERADOS[clase])

    def test_los_codigos_de_tipo_caben_en_la_columna(self):
        """``tipo_evento.codigo`` es CharField(max_length=20)."""
        for vigilada in config.yolo.clases_vigiladas:
            with self.subTest(clase=vigilada.clase):
                self.assertLessEqual(len(vigilada.tipo_evento), 20)
                self.assertLessEqual(len(vigilada.nombre), 60)

    def test_una_clase_desconocida_no_se_vigila(self):
        self.assertIsNone(config.yolo.vigilada("jirafa"))
        self.assertIsNone(config.yolo.tipo_evento_de("jirafa"))

    def test_la_siembra_incluye_los_tipos_de_vision(self):
        SembradorCatalogos(lambda _: None).sembrar_tipos_evento()
        for codigo in TIPOS_ESPERADOS.values():
            with self.subTest(codigo=codigo):
                self.assertTrue(
                    TipoEvento.objects.filter(codigo=codigo).exists(),
                    f"sembrar_catalogos no creó el tipo {codigo}",
                )

    def test_la_siembra_no_duplica_persona(self):
        SembradorCatalogos(lambda _: None).sembrar_tipos_evento()
        self.assertEqual(TipoEvento.objects.filter(codigo="PERSONA").count(), 1)


class ResultadoDeteccionTests(TestCase):
    """Umbral **por clase** y elección de la detección principal."""

    def test_una_caja_vigilada_sobre_su_umbral_genera_alerta(self):
        resultado = ResultadoDeteccion(cajas=[_caja("arma_fuego", 0.80)])
        self.assertTrue(resultado.hay_alerta)
        self.assertEqual(resultado.tipo_evento_principal, "ARMA_FUEGO")
        self.assertEqual(resultado.num_detecciones, 1)

    def test_una_caja_por_debajo_de_su_umbral_no_alerta(self):
        umbral = config.yolo.umbral_alerta("suspicious")
        resultado = ResultadoDeteccion(cajas=[_caja("suspicious", umbral - 0.1)])
        self.assertFalse(resultado.hay_alerta)
        self.assertIsNone(resultado.tipo_evento_principal)
        self.assertEqual(resultado.num_detecciones, 0)

    def test_una_clase_no_vigilada_no_alerta(self):
        resultado = ResultadoDeteccion(cajas=[_caja("jirafa", 0.99)])
        self.assertFalse(resultado.hay_alerta)
        self.assertEqual(resultado.clases_detectadas, [])

    def test_gana_la_deteccion_de_mayor_confianza(self):
        resultado = ResultadoDeteccion(cajas=[_caja("suspicious", 0.70), _caja("arma_fuego", 0.95)])
        self.assertEqual(resultado.clase_principal, "arma_fuego")
        self.assertEqual(resultado.tipo_evento_principal, "ARMA_FUEGO")
        self.assertEqual(resultado.clases_detectadas, ["arma_fuego", "suspicious"])

    def test_resumen_de_clases_para_telegram(self):
        resultado = ResultadoDeteccion(cajas=[_caja("arma_blanca", 0.86)])
        self.assertEqual(resultado.resumen_clases, "arma_blanca 86%")

    def test_hay_persona_sigue_siendo_literal(self):
        """``hay_persona`` no se dispara con las clases nuevas: PERSONA tiene
        su propia regla (solo ESTACIONADO y armado)."""
        resultado = ResultadoDeteccion(cajas=[_caja("arma_fuego", 0.99)])
        self.assertFalse(resultado.hay_persona)
        self.assertTrue(resultado.hay_alerta)


class AlertaPorClaseEnPipelineTests(TestCase):
    """Pipeline completo con dobles: cada clase debe acabar en Telegram."""

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
        SembradorCatalogos(lambda _: None).sembrar_tipos_evento()

    def _registrar(self, clase: str, confianza: float = 0.9):
        difusor = DifusorAlertasFalso()
        servicio = IncidenciaService(
            difusor_alertas=difusor, fabrica_camara=FabricaCamaraFalsa(exito=True),
        )
        ahora = timezone.now()
        incidencia = servicio.registrar_incidencia_visual(
            dispositivo=self.dispositivo,
            captura=ResultadoCaptura(exito=True, jpg=b"jpeg-falso", metodo="stream", captura_ms=12.0),
            resultado_yolo=ResultadoDeteccion(cajas=[_caja(clase, confianza)]),
            tipo_codigo=config.yolo.tipo_evento_de(clase), clase_yolo=clase,
            t_deteccion_sensor=ahora, t_recepcion_servidor=ahora,
            t_inicio_inferencia=ahora, t_fin_inferencia=ahora,
        )
        return incidencia, difusor

    def test_cada_clase_genera_incidencia_y_alerta(self):
        for clase in CLASES_DEL_MODELO:
            with self.subTest(clase=clase):
                Incidencia.objects.all().delete()
                incidencia, difusor = self._registrar(clase)

                self.assertEqual(incidencia.tipo.codigo, TIPOS_ESPERADOS[clase])
                self.assertEqual(difusor.llamadas, [incidencia.pk], "no se difundió la alerta")
                self.assertEqual(difusor.silenciosas, [False], "la alerta llegó silenciada")
                self.assertEqual(incidencia.estado, "NOTIFICADA")
                self.assertEqual(AlertaTelegram.objects.filter(incidencia=incidencia).count(), 1)

    def test_la_observacion_registra_la_clase_y_la_confianza(self):
        incidencia, _ = self._registrar("arma_blanca", 0.86)
        self.assertIn("arma_blanca", incidencia.observacion)
        self.assertIn("86%", incidencia.observacion)

    def test_la_deteccion_queda_guardada_con_su_clase(self):
        incidencia, _ = self._registrar("car broken", 0.77)
        deteccion = incidencia.detecciones.get()
        self.assertEqual(deteccion.clase, "car broken")
        self.assertAlmostEqual(deteccion.confianza, 0.77)

    def test_el_tipo_se_siembra_si_falta_en_el_catalogo(self):
        """Un catálogo viejo no debe hacer que la alerta se pierda en silencio."""
        TipoEvento.objects.filter(codigo="ARMA_FUEGO").delete()
        incidencia, difusor = self._registrar("arma_fuego")

        self.assertEqual(incidencia.tipo.codigo, "ARMA_FUEGO")
        self.assertEqual(incidencia.tipo.nivel_riesgo, "ALTO")
        self.assertEqual(difusor.llamadas, [incidencia.pk])

    def test_bajar_el_tipo_a_riesgo_bajo_deja_de_alertar(self):
        """La política sigue siendo del catálogo, editable desde la web."""
        TipoEvento.objects.filter(codigo="PERSONA_SOSPECHOSA").update(nivel_riesgo="BAJO")
        incidencia, difusor = self._registrar("suspicious")

        self.assertEqual(difusor.llamadas, [])
        self.assertEqual(incidencia.estado, "PENDIENTE")

    def test_un_evento_de_sensor_se_reclasifica_a_lo_que_ve_yolo(self):
        from monitoreo.tests.test_pipeline import DetectorYoloFalso

        TipoEvento.objects.update_or_create(
            codigo="GOLPE_MANIPULACION",
            defaults={"nombre": "Golpe / manipulación", "nivel_riesgo": "MEDIO"},
        )
        difusor = DifusorAlertasFalso()
        servicio = IncidenciaService(
            difusor_alertas=difusor, fabrica_camara=FabricaCamaraFalsa(exito=True),
            detector_yolo=DetectorYoloFalso(cajas=[_caja("arma_fuego", 0.93)]),
        )
        ahora_ms = int(timezone.now().timestamp() * 1000)
        incidencia = servicio.procesar_evento_sensor({
            "codigo": "NODO-01", "evento_uid": "UID-1", "tipo": "GOLPE_MANIPULACION",
            "ts_ms": ahora_ms, "t_recepcion_ms": ahora_ms, "evidencia": {"vib_g": 0.42},
        })

        self.assertEqual(incidencia.tipo.codigo, "ARMA_FUEGO")
        self.assertIn("Disparado por sensor: GOLPE_MANIPULACION", incidencia.observacion)
        # ARMA_FUEGO es ALTO: el aviso silencioso de GOLPE_MANIPULACION se vuelve alerta.
        self.assertEqual(difusor.silenciosas, [False])


class ConfirmacionPorClaseTests(TestCase):
    """``VigilanciaVisual``: racha y cooldown independientes por clase."""

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
        SembradorCatalogos(lambda _: None).sembrar_tipos_evento()
        EstadoEnVivo._instancia = None
        self.estado_en_vivo = EstadoEnVivo.instancia()

    def tearDown(self):
        EstadoEnVivo._instancia = None

    def _vigilancia(self):
        """Con un difusor falso: la suite nunca llama a la API real de Telegram."""
        from monitoreo.workers.vigilancia_visual import VigilanciaVisual

        self.difusor = DifusorAlertasFalso()
        return VigilanciaVisual(
            incidencia_service=IncidenciaService(
                difusor_alertas=self.difusor, fabrica_camara=FabricaCamaraFalsa(exito=True),
            ),
        )

    def _frame(self, *clases):
        from monitoreo.workers.vigilancia_visual import FrameProcesado

        ahora = timezone.now()
        return FrameProcesado(
            ahora, ahora, ahora, ahora,
            ResultadoCaptura(exito=True, jpg=b"jpeg-falso", metodo="stream"),
            ResultadoDeteccion(cajas=[_caja(c, 0.9) for c in clases]),
        )

    def test_un_arma_alerta_aunque_el_vehiculo_este_en_marcha(self):
        """Un arma es una emergencia en cualquier modo (y sin depender del armado)."""
        self.estado_en_vivo.actualizar_modo("NODO-01", "EN_MARCHA", "INERCIAL")
        vigilancia = self._vigilancia()
        frame = self._frame("arma_fuego")

        for _ in range(config.vision.frames_confirmacion):
            vigilancia._evaluar_confirmaciones(self.dispositivo, frame)

        self.assertEqual(Incidencia.objects.count(), 1)
        self.assertEqual(Incidencia.objects.get().tipo.codigo, "ARMA_FUEGO")

    def test_un_solo_cuadro_no_basta(self):
        """Hacen falta VISION_FRAMES_CONFIRMACION cuadros seguidos."""
        if config.vision.frames_confirmacion < 2:
            self.skipTest("VISION_FRAMES_CONFIRMACION=1: no hay confirmación que probar")
        self._vigilancia()._evaluar_confirmaciones(self.dispositivo, self._frame("arma_blanca"))
        self.assertEqual(Incidencia.objects.count(), 0)

    def test_la_racha_se_pierde_si_la_clase_desaparece(self):
        vigilancia = self._vigilancia()
        vigilancia._evaluar_confirmaciones(self.dispositivo, self._frame("arma_blanca"))
        vigilancia._evaluar_confirmaciones(self.dispositivo, self._frame("car broken"))

        self.assertIsNone(vigilancia._frames_confirmados.get(("NODO-01", "arma_blanca")))
        self.assertEqual(Incidencia.objects.count(), 0)

    def test_dos_clases_a_la_vez_generan_dos_incidencias(self):
        """Cada clase lleva su propia racha: una no roba los cuadros de la otra."""
        vigilancia = self._vigilancia()
        frame = self._frame("arma_fuego", "car broken")

        for _ in range(config.vision.frames_confirmacion):
            vigilancia._evaluar_confirmaciones(self.dispositivo, frame)

        tipos = set(Incidencia.objects.values_list("tipo__codigo", flat=True))
        self.assertEqual(tipos, {"ARMA_FUEGO", "VEHICULO_DANADO"})

    def test_el_cooldown_es_por_clase(self):
        vigilancia = self._vigilancia()
        for _ in range(config.vision.frames_confirmacion * 2):
            vigilancia._evaluar_confirmaciones(self.dispositivo, self._frame("arma_fuego"))

        # El cooldown corta la segunda incidencia de arma_fuego...
        self.assertEqual(Incidencia.objects.filter(tipo__codigo="ARMA_FUEGO").count(), 1)

        # ...pero no afecta a otra clase.
        for _ in range(config.vision.frames_confirmacion):
            vigilancia._evaluar_confirmaciones(self.dispositivo, self._frame("arma_blanca"))
        self.assertEqual(Incidencia.objects.filter(tipo__codigo="ARMA_BLANCA").count(), 1)

    def test_una_deteccion_floja_no_crea_incidencia(self):
        from monitoreo.workers.vigilancia_visual import FrameProcesado

        umbral = config.yolo.umbral_alerta("arma_fuego")
        ahora = timezone.now()
        frame = FrameProcesado(
            ahora, ahora, ahora, ahora,
            ResultadoCaptura(exito=True, jpg=b"jpeg-falso", metodo="stream"),
            ResultadoDeteccion(cajas=[_caja("arma_fuego", umbral - 0.05)]),
        )
        vigilancia = self._vigilancia()
        for _ in range(config.vision.frames_confirmacion + 1):
            vigilancia._evaluar_confirmaciones(self.dispositivo, frame)

        self.assertEqual(Incidencia.objects.count(), 0)
