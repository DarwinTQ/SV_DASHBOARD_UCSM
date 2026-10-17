"""Pruebas del buffer de cuadros y del proveedor de imagen (sección 5.5).

La idea que se valida aquí es la que baja el tiempo de respuesta del sistema:
la incidencia usa el cuadro del stream **más cercano al instante del sensor**,
en vez de pedir una foto nueva a la ESP32-CAM.
"""
from django.test import TestCase

from monitoreo.services.buffer_cuadros import BufferCuadros, CuadroStream, RegistroBuffers
from monitoreo.services.camara_service import ProveedorCuadros, ResultadoCaptura


class CamaraFalsa:
    def __init__(self):
        self.capturas = 0

    def capturar(self, con_flash=False):
        self.capturas += 1
        return ResultadoCaptura(exito=True, jpg=b"de-capture", captura_ms=340.0, metodo="captura")


class FabricaCamaraFalsa:
    def __init__(self):
        self.camara = CamaraFalsa()

    def para(self, codigo):
        return self.camara


class BufferCuadrosTests(TestCase):
    def _buffer(self, segundos=3.0):
        return BufferCuadros("NODO-01", segundos=segundos)

    def test_guarda_y_devuelve_el_ultimo(self):
        buffer = self._buffer()
        buffer.agregar(CuadroStream(jpg=b"a", t_recepcion_ms=1000))
        buffer.agregar(CuadroStream(jpg=b"b", t_recepcion_ms=1200))

        self.assertEqual(buffer.ultimo().jpg, b"b")
        self.assertEqual(buffer.cantidad(), 2)

    def test_purga_los_cuadros_mas_viejos_que_la_ventana(self):
        buffer = self._buffer(segundos=3.0)
        buffer.agregar(CuadroStream(jpg=b"viejo", t_recepcion_ms=1000))
        buffer.agregar(CuadroStream(jpg=b"medio", t_recepcion_ms=3000))
        # Este cuadro deja al primero fuera de los 3 s de ventana.
        buffer.agregar(CuadroStream(jpg=b"nuevo", t_recepcion_ms=4500))

        self.assertEqual(buffer.cantidad(), 2)
        self.assertEqual(buffer.descartados, 1)
        self.assertEqual(buffer.ultimo().jpg, b"nuevo")

    def test_elige_el_cuadro_mas_cercano_al_instante_pedido(self):
        buffer = self._buffer()
        for ts, jpg in [(1000, b"a"), (1200, b"b"), (1400, b"c"), (1600, b"d")]:
            buffer.agregar(CuadroStream(jpg=jpg, t_recepcion_ms=ts))

        # 1370 está a 30 ms de 1400 y a 170 ms de 1200.
        self.assertEqual(buffer.mas_cercano_a(1370).jpg, b"c")
        self.assertEqual(buffer.mas_cercano_a(1000).jpg, b"a")
        # Fuera de rango: el extremo más próximo.
        self.assertEqual(buffer.mas_cercano_a(9999).jpg, b"d")

    def test_sin_instante_devuelve_el_mas_reciente(self):
        """El nodo puede mandar ts_ms nulo si aún no tiene reloj."""
        buffer = self._buffer()
        buffer.agregar(CuadroStream(jpg=b"a", t_recepcion_ms=1000))
        buffer.agregar(CuadroStream(jpg=b"b", t_recepcion_ms=1200))
        self.assertEqual(buffer.mas_cercano_a(None).jpg, b"b")

    def test_buffer_vacio_no_devuelve_nada(self):
        buffer = self._buffer()
        self.assertIsNone(buffer.ultimo())
        self.assertIsNone(buffer.mas_cercano_a(1000))


class RegistroBuffersTests(TestCase):
    def setUp(self):
        RegistroBuffers._instancia = None

    def tearDown(self):
        RegistroBuffers._instancia = None

    def test_para_crea_uno_por_codigo_y_lo_reutiliza(self):
        registro = RegistroBuffers.instancia()
        primero = registro.para("NODO-01")
        self.assertIs(registro.para("NODO-01"), primero)
        self.assertIsNot(registro.para("NODO-02"), primero)

    def test_existente_no_crea_el_buffer(self):
        """Así el proveedor distingue "hay stream" de "no hay stream"."""
        registro = RegistroBuffers.instancia()
        self.assertIsNone(registro.existente("NODO-01"))
        registro.para("NODO-01")
        self.assertIsNotNone(registro.existente("NODO-01"))


class ProveedorCuadrosTests(TestCase):
    def setUp(self):
        RegistroBuffers._instancia = None
        self.registro = RegistroBuffers.instancia()
        self.fabrica = FabricaCamaraFalsa()
        self.proveedor = ProveedorCuadros(
            fabrica_camara=self.fabrica, registro_buffers=self.registro,
        )

    def tearDown(self):
        RegistroBuffers._instancia = None

    def test_sin_stream_cae_a_capture(self):
        resultado = self.proveedor.mas_reciente("NODO-01")

        self.assertTrue(resultado.exito)
        self.assertEqual(resultado.metodo, "captura")
        self.assertEqual(self.fabrica.camara.capturas, 1)

    def test_con_stream_usa_el_buffer_y_no_llama_a_la_camara(self):
        buffer = self.registro.para("NODO-01")
        buffer.agregar(CuadroStream(jpg=b"del-stream", t_recepcion_ms=5000))

        resultado = self.proveedor.mas_reciente("NODO-01")

        self.assertEqual(resultado.jpg, b"del-stream")
        self.assertEqual(resultado.metodo, "stream")
        # La mejora medible: cero milisegundos de captura y cero peticiones HTTP.
        self.assertEqual(resultado.captura_ms, 0.0)
        self.assertEqual(self.fabrica.camara.capturas, 0)

    def test_cercano_a_reporta_el_desfase_con_el_instante_del_sensor(self):
        buffer = self.registro.para("NODO-01")
        buffer.agregar(CuadroStream(jpg=b"a", t_recepcion_ms=5000))
        buffer.agregar(CuadroStream(jpg=b"b", t_recepcion_ms=5200))

        resultado = self.proveedor.cercano_a("NODO-01", 5180)

        self.assertEqual(resultado.jpg, b"b")
        self.assertEqual(resultado.desfase_ms, 20)
        self.assertEqual(resultado.metodo, "stream")

    def test_buffer_vacio_cae_a_capture(self):
        """El stream existe pero se acaba de caer: hay respaldo."""
        self.registro.para("NODO-01")
        resultado = self.proveedor.cercano_a("NODO-01", 5000)

        self.assertEqual(resultado.metodo, "captura")
        self.assertEqual(self.fabrica.camara.capturas, 1)


class NotaDeImagenEnObservacionTests(TestCase):
    """El método usado para la imagen queda registrado en ``observacion``."""

    def test_nota_del_stream_incluye_el_desfase(self):
        from monitoreo.services.incidencia_service import IncidenciaService

        nota = IncidenciaService._nota_imagen(
            ResultadoCaptura(exito=True, jpg=b"x", metodo="stream", desfase_ms=37.0),
        )
        self.assertIn("stream MJPEG", nota)
        self.assertIn("37 ms", nota)

    def test_nota_de_capture_lo_dice_explicitamente(self):
        from monitoreo.services.incidencia_service import IncidenciaService

        nota = IncidenciaService._nota_imagen(
            ResultadoCaptura(exito=True, jpg=b"x", metodo="captura"),
        )
        self.assertIn("/capture", nota)

    def test_sin_imagen_no_hay_nota(self):
        from monitoreo.services.incidencia_service import IncidenciaService

        self.assertEqual(IncidenciaService._nota_imagen(ResultadoCaptura(exito=False)), "")
