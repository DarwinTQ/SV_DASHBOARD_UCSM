"""Lee en continuo el stream MJPEG de cada ESP32-CAM (sección 5.5).

Un hilo por cámara mantiene abierta la conexión HTTP a
``ESP32CAM_STREAM_URLS`` (puerto 81 del firmware v3.0.0), separa los JPEG por
el *boundary* del multipart y los va dejando en ``BufferCuadros``.

Por qué esto baja el tiempo de respuesta: antes cada incidencia pedía una foto
nueva con ``GET /capture`` y había que esperar la captura + la transferencia
(cientos de ms). Ahora el cuadro de ese instante **ya está** en memoria, así que
el pipeline solo tiene que elegirlo.

Si una cámara no tiene URL de stream configurada, o el stream se cae, el worker
reintenta solo y el pipeline cae a ``/capture`` (queda anotado en
``observacion`` de la incidencia qué método se usó).
"""
from __future__ import annotations

import threading
import time

import requests

from config import config
from monitoreo.services.buffer_cuadros import CuadroStream, RegistroBuffers
from monitoreo.services.reloj_service import RelojServidor

from .base_worker import WorkerBase

# Marcadores JPEG: SOI (inicio de imagen) y EOI (fin de imagen).
JPEG_INICIO = b"\xff\xd8"
JPEG_FIN = b"\xff\xd9"
TAMANO_LECTURA = 8192
# Un JPEG VGA con calidad 12 pesa ~40 kB; 512 kB es un tope de seguridad por si
# el stream se desincroniza y nunca aparece el fin de imagen.
TAMANO_MAXIMO_CUADRO = 512 * 1024


class LectorStreamCamara:
    """Lee el MJPEG de UNA cámara en su propio hilo, con reconexión."""

    def __init__(self, codigo: str, url: str, detener: threading.Event, logger):
        self.codigo = codigo
        self.url = url
        self._detener = detener
        self._logger = logger
        self._buffer = RegistroBuffers.instancia().para(codigo)
        self.conectado = False
        self.reconexiones = 0
        self.cuadros = 0
        self._hilo: threading.Thread | None = None

    def iniciar(self) -> None:
        self._hilo = threading.Thread(
            target=self._ejecutar, name=f"stream-{self.codigo}", daemon=True,
        )
        self._hilo.start()

    def esperar(self, timeout: float = 3.0) -> None:
        if self._hilo is not None:
            self._hilo.join(timeout=timeout)

    # ------------------------------------------------------------------ #
    def _ejecutar(self) -> None:
        while not self._detener.is_set():
            try:
                self._leer_stream()
            except requests.RequestException as exc:
                self._logger.warning("Stream de %s cortado (%s); se reintenta en %.0f s.",
                                     self.codigo, exc, config.stream.reconexion_s)
            except Exception:
                self._logger.exception("Error inesperado leyendo el stream de %s", self.codigo)
            finally:
                self.conectado = False

            if self._detener.is_set():
                return
            self.reconexiones += 1
            self._detener.wait(config.stream.reconexion_s)

    def _leer_stream(self) -> None:
        # La cámara autoriza el stream por ?key= (una conexión persistente no
        # puede repetir la cabecera X-API-Key en cada cuadro).
        separador = "&" if "?" in self.url else "?"
        url = f"{self.url}{separador}key={config.esp32cam.api_key}"

        with requests.get(url, stream=True, timeout=config.stream.timeout_s) as respuesta:
            respuesta.raise_for_status()
            self.conectado = True
            self._logger.info("Stream de %s conectado: %s", self.codigo, self.url)
            self._consumir(respuesta)

    def _consumir(self, respuesta) -> None:
        """Acumula bytes y corta en cada par SOI…EOI.

        Se busca por marcadores JPEG en vez de parsear el ``Content-Length`` del
        multipart porque así el lector tolera que la cámara reinicie el stream
        a mitad de un cuadro.
        """
        acumulado = bytearray()
        for trozo in respuesta.iter_content(chunk_size=TAMANO_LECTURA):
            if self._detener.is_set():
                return
            if not trozo:
                continue
            acumulado.extend(trozo)

            while True:
                inicio = acumulado.find(JPEG_INICIO)
                if inicio < 0:
                    # Todavía no empezó una imagen: se conserva solo la cola por
                    # si el marcador quedó partido entre dos trozos.
                    if len(acumulado) > 2:
                        del acumulado[:-1]
                    break
                fin = acumulado.find(JPEG_FIN, inicio + 2)
                if fin < 0:
                    if inicio > 0:
                        del acumulado[:inicio]
                    if len(acumulado) > TAMANO_MAXIMO_CUADRO:
                        self._logger.warning(
                            "Cuadro de %s sin fin de imagen tras %d kB; se descarta el buffer.",
                            self.codigo, len(acumulado) // 1024,
                        )
                        acumulado.clear()
                    break

                jpg = bytes(acumulado[inicio:fin + 2])
                del acumulado[:fin + 2]
                self._guardar(jpg)

    def _guardar(self, jpg: bytes) -> None:
        self._buffer.agregar(CuadroStream(jpg=jpg, t_recepcion_ms=RelojServidor.ahora_ms()))
        self.cuadros += 1

    def resumen(self) -> dict:
        buffer = self._buffer.resumen()
        return {
            "codigo": self.codigo, "url": self.url, "conectado": self.conectado,
            "cuadros_leidos": self.cuadros, "reconexiones": self.reconexiones,
            # Estado del buffer circular (lo que ve el pipeline ahora mismo).
            "cuadros_buffer": buffer["cuadros"], "ventana_ms": buffer["ventana_ms"],
            "bytes_ultimo": buffer["bytes_ultimo"], "descartados": buffer["descartados"],
        }


class ReceptorStreamCamara(WorkerBase):
    """Levanta un ``LectorStreamCamara`` por cada URL de ``ESP32CAM_STREAM_URLS``."""

    nombre = "ReceptorStreamCamara"

    # Instancia viva del proceso (la que arrancó main.py); la lee la página
    # Configuración. Es None en manage.py y en las pruebas.
    _activa: "ReceptorStreamCamara | None" = None

    @classmethod
    def resumen_streams(cls) -> list:
        """Resumen por cámara para la página Configuración (nunca lanza)."""
        return cls._activa.resumen() if cls._activa is not None else []

    def __init__(self):
        super().__init__()
        self._lectores: dict[str, LectorStreamCamara] = {}

    def iniciar(self) -> None:
        urls = config.esp32cam.urls_stream_por_dispositivo
        if config.esp32cam.simulada:
            self.logger.info(
                "Cámara simulada: no se abre ningún stream MJPEG "
                "(VigilanciaVisual usará media/simulacion/*.jpg)."
            )
            return
        if not urls:
            self.logger.info(
                "ESP32CAM_STREAM_URLS vacío: el pipeline seguirá pidiendo fotos a /capture. "
                "Configúralo para usar el stream continuo (menor tiempo de respuesta)."
            )
            return

        super().iniciar()
        ReceptorStreamCamara._activa = self
        for codigo, url in urls.items():
            lector = LectorStreamCamara(codigo, url, self._detener, self.logger)
            self._lectores[codigo] = lector
            lector.iniciar()
        self.logger.info("Receptor de stream activo para: %s", ", ".join(urls))

    def detener(self) -> None:
        super().detener()
        for lector in self._lectores.values():
            lector.esperar()
        if ReceptorStreamCamara._activa is self:
            ReceptorStreamCamara._activa = None

    def ciclo(self) -> None:
        """Los lectores tienen su propio hilo; aquí solo se registra la salud."""
        time.sleep(15)
        for lector in self._lectores.values():
            if not lector.conectado:
                self.logger.warning("Stream de %s sin conexión (reconexiones: %d)",
                                    lector.codigo, lector.reconexiones)

    def resumen(self) -> list:
        """Lo muestra la página Configuración."""
        return [lector.resumen() for lector in self._lectores.values()]
