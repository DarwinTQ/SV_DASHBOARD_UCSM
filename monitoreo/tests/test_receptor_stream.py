"""Pruebas del separador de cuadros del stream MJPEG (sección 5.5).

El lector recibe los bytes en trozos arbitrarios (lo que dé la red) y tiene que
reconstruir cada JPEG. Aquí se simula una respuesta ``requests`` partiendo el
multipart en trozos adrede, incluyendo el caso de un marcador cortado entre dos
trozos, que es el que rompe las implementaciones ingenuas.
"""
import threading

from django.test import TestCase

from monitoreo.services.buffer_cuadros import RegistroBuffers
from monitoreo.workers.receptor_stream import JPEG_FIN, JPEG_INICIO, LectorStreamCamara

SEPARADOR = b"\r\n--cuadroUCSM\r\n"


def cuadro_falso(relleno: bytes) -> bytes:
    """Un JPEG mínimo pero válido para el separador: SOI … EOI."""
    return JPEG_INICIO + relleno + JPEG_FIN


def multipart(cuadros: list) -> bytes:
    partes = []
    for i, cuadro in enumerate(cuadros):
        partes.append(SEPARADOR)
        partes.append(
            b"Content-Type: image/jpeg\r\nContent-Length: %d\r\nX-Timestamp-Ms: %d\r\n\r\n"
            % (len(cuadro), 1000 + i * 125)
        )
        partes.append(cuadro)
    return b"".join(partes)


class RespuestaFalsa:
    """Imita ``requests.Response.iter_content`` con trozos a medida."""

    def __init__(self, trozos):
        self._trozos = trozos

    def iter_content(self, chunk_size=8192):
        yield from self._trozos


class LectorStreamTests(TestCase):
    def setUp(self):
        RegistroBuffers._instancia = None
        import logging

        self.detener = threading.Event()
        self.lector = LectorStreamCamara(
            "NODO-01", "http://10.0.0.1:81/stream", self.detener,
            logging.getLogger("pruebas.stream"),
        )
        self.buffer = RegistroBuffers.instancia().para("NODO-01")

    def tearDown(self):
        RegistroBuffers._instancia = None

    def test_separa_los_cuadros_de_un_multipart_completo(self):
        cuerpo = multipart([cuadro_falso(b"uno"), cuadro_falso(b"dos"), cuadro_falso(b"tres")])
        self.lector._consumir(RespuestaFalsa([cuerpo]))

        self.assertEqual(self.lector.cuadros, 3)
        self.assertEqual(self.buffer.ultimo().jpg, cuadro_falso(b"tres"))

    def test_tolera_que_los_trozos_corten_los_cuadros(self):
        cuerpo = multipart([cuadro_falso(b"a" * 50), cuadro_falso(b"b" * 50)])
        trozos = [cuerpo[i:i + 7] for i in range(0, len(cuerpo), 7)]   # trozos de 7 bytes
        self.lector._consumir(RespuestaFalsa(trozos))

        self.assertEqual(self.lector.cuadros, 2)
        self.assertEqual(self.buffer.ultimo().jpg, cuadro_falso(b"b" * 50))

    def test_tolera_un_marcador_partido_entre_dos_trozos(self):
        """El 0xFF y el 0xD8 del SOI pueden llegar en trozos distintos."""
        cuadro = cuadro_falso(b"partido")
        cuerpo = multipart([cuadro])
        corte = cuerpo.find(JPEG_INICIO) + 1
        self.lector._consumir(RespuestaFalsa([cuerpo[:corte], cuerpo[corte:]]))

        self.assertEqual(self.lector.cuadros, 1)
        self.assertEqual(self.buffer.ultimo().jpg, cuadro)

    def test_ignora_basura_antes_del_primer_cuadro(self):
        cuerpo = b"HTTP/1.1 200 OK\r\nbasura\r\n" + multipart([cuadro_falso(b"ok")])
        self.lector._consumir(RespuestaFalsa([cuerpo]))

        self.assertEqual(self.lector.cuadros, 1)
        self.assertEqual(self.buffer.ultimo().jpg, cuadro_falso(b"ok"))

    def test_un_cuadro_sin_fin_de_imagen_no_se_entrega(self):
        cuerpo = SEPARADOR + b"Content-Type: image/jpeg\r\n\r\n" + JPEG_INICIO + b"incompleto"
        self.lector._consumir(RespuestaFalsa([cuerpo]))

        self.assertEqual(self.lector.cuadros, 0)
        self.assertIsNone(self.buffer.ultimo())

    def test_la_senal_de_parada_corta_la_lectura(self):
        cuerpo = multipart([cuadro_falso(b"uno"), cuadro_falso(b"dos")])
        self.detener.set()
        self.lector._consumir(RespuestaFalsa([cuerpo]))

        self.assertEqual(self.lector.cuadros, 0)
