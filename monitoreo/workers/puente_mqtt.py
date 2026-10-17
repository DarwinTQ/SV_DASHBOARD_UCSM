"""Puente entre el broker MQTT y el resto del sistema (sección 5.3).

Es el reemplazo del camino HTTP: todo lo que el nodo y la cámara publican entra
por aquí y se reparte a los servicios que ya existían (``TelemetriaService``,
``EstadoEnVivo``, ``ProcesadorEventos``, ``NotificadorTiempoReal``). Los
endpoints ``/api/v1/telemetria/`` y ``/api/v1/eventos/`` se mantienen como
respaldo y comparten exactamente los mismos servicios.

Detalles que importan para la tesis:

* ``t_recepcion_ms`` se toma en ``ClienteMqtt._on_message``, **antes** de
  parsear el JSON, para que ``medicion_tiempo.t_recepcion_servidor`` mida la
  red y no el trabajo del servidor.
* ``sync/req`` se responde de inmediato, sin tocar la base de datos: cualquier
  consulta añadiría milisegundos al RTT con el que el nodo calcula su offset.
* ``estado`` (incluido el *Last Will* del broker) marca el dispositivo en línea
  o fuera de línea al instante; ``MonitorDispositivos`` queda solo como
  respaldo por timeout.

Los callbacks de paho corren en **su propio hilo**, así que cada handler cierra
las conexiones viejas de Django al terminar (``close_old_connections``).
"""
from __future__ import annotations

import csv
import time
from pathlib import Path

from django.db import close_old_connections
from django.utils import timezone

from config import config
from monitoreo.serializers import (
    EstadoCamaraSerializer, EstadoNodoSerializer, EventoSerializer, GpsPosicionSerializer,
    ModoSerializer, SyncPeticionSerializer, TelemetriaSerializer,
)
from monitoreo.services.estado_en_vivo import EstadoEnVivo
from monitoreo.services.mqtt_service import ClienteMqtt, MensajeMqtt, PublicadorConfiguracion
from monitoreo.services.notificador_tiempo_real import NotificadorTiempoReal

from .base_worker import WorkerBase

CICLO_S = 5.0
CACHE_DISPOSITIVOS_S = 10.0


class CacheDispositivos:
    """Evita una consulta a SQLite por cada mensaje GPS (llegan a 5 Hz)."""

    def __init__(self, vigencia_s: float = CACHE_DISPOSITIVOS_S):
        self._vigencia_s = vigencia_s
        self._cache: dict[str, tuple[float, object]] = {}

    def obtener(self, codigo: str):
        from monitoreo.models import Dispositivo

        entrada = self._cache.get(codigo)
        ahora = time.monotonic()
        if entrada and ahora - entrada[0] < self._vigencia_s:
            return entrada[1]

        dispositivo = (
            Dispositivo.objects.filter(codigo=codigo)
            .select_related("vehiculo", "vehiculo__conductor").first()
        )
        self._cache[codigo] = (ahora, dispositivo)
        return dispositivo

    def invalidar(self, codigo: str = "") -> None:
        if codigo:
            self._cache.pop(codigo, None)
        else:
            self._cache.clear()


class RegistroGpsCsv:
    """Vuelca el rastro a ``logs/gps_track.csv`` (opcional, para la tesis)."""

    CABECERA = ["fecha_hora", "codigo", "lat", "lon", "vel_kmh", "rumbo_deg", "sats", "hdop", "modo", "ts_ms"]

    def __init__(self, ruta: Path):
        self._ruta = ruta
        self._iniciado = False

    def registrar(self, codigo: str, gps: dict) -> None:
        self._ruta.parent.mkdir(parents=True, exist_ok=True)
        nuevo = not self._ruta.exists()
        with self._ruta.open("a", newline="", encoding="utf-8") as archivo:
            escritor = csv.writer(archivo)
            if nuevo or not self._iniciado and self._ruta.stat().st_size == 0:
                escritor.writerow(self.CABECERA)
            self._iniciado = True
            escritor.writerow([
                timezone.localtime().isoformat(timespec="milliseconds"), codigo,
                gps.get("lat"), gps.get("lon"), gps.get("vel_kmh"), gps.get("rumbo_deg"),
                gps.get("sats"), gps.get("hdop"), gps.get("modo"), gps.get("ts_ms"),
            ])


class PuenteMQTT(WorkerBase):
    """Suscribe el servidor al broker y despacha cada tópico a su servicio."""

    nombre = "PuenteMQTT"

    # Instancia viva del proceso (la que arrancó main.py). La página
    # Configuración la consulta para mostrar la salud del enlace; es None en
    # manage.py y en las pruebas, donde no hay broker.
    _activa: "PuenteMQTT | None" = None

    @classmethod
    def activa(cls) -> "PuenteMQTT | None":
        return cls._activa

    @classmethod
    def resumen_estado(cls) -> dict:
        """Resumen para la página Configuración (nunca lanza)."""
        puente = cls._activa
        if puente is None:
            return {
                "activo": False, "conectado": False, "host": config.mqtt.host,
                "puerto": config.mqtt.puerto, "recibidos": 0, "publicados": 0,
                "reconexiones": 0, "invalidos": 0, "duplicados": 0, "sin_dispositivo": 0,
            }
        cliente = puente.cliente
        return {
            "activo": True, "conectado": cliente.conectado(),
            "host": config.mqtt.host, "puerto": config.mqtt.puerto,
            "recibidos": cliente.recibidos, "publicados": cliente.publicados,
            "reconexiones": cliente.reconexiones, "invalidos": cliente.invalidos,
            "duplicados": puente._eventos_duplicados,
            "sin_dispositivo": puente._sin_dispositivo,
        }

    def __init__(self, cliente: ClienteMqtt | None = None):
        super().__init__()
        self._estado_en_vivo = EstadoEnVivo.instancia()
        self._dispositivos = CacheDispositivos()
        self._publicador = PublicadorConfiguracion.instancia()
        self._registro_gps = (
            RegistroGpsCsv(config.BASE_DIR / "logs" / "gps_track.csv")
            if config.mqtt.registrar_gps_csv else None
        )
        self._cliente = cliente or ClienteMqtt(
            al_recibir=self.procesar_mensaje, al_conectar=self._al_conectar,
        )
        self._manejadores = {
            "telemetria": self._manejar_telemetria,
            "gps": self._manejar_gps,
            "evento": self._manejar_evento,
            "estado": self._manejar_estado,
            "modo": self._manejar_modo,
            "sync/req": self._manejar_sync,
            "cam/estado": self._manejar_camara_estado,
        }
        self._eventos_duplicados = 0
        self._sin_dispositivo = 0
        # Se comparte la conexión con el publicador desde ya (no en iniciar):
        # así la web, el bot y la propia publicación de 'estado' pueden enviar
        # 'config' aunque todavía no se haya arrancado el hilo del worker.
        self._publicador.registrar_cliente(self._cliente)

    @property
    def cliente(self) -> ClienteMqtt:
        return self._cliente

    # ------------------------------------------------------------------ #
    # Ciclo de vida del worker
    # ------------------------------------------------------------------ #
    def iniciar(self) -> None:
        if not self._cliente.conectar():
            self.logger.error("El puente MQTT no arrancó; el sistema seguirá con el respaldo HTTP.")
            return
        PuenteMQTT._activa = self
        super().iniciar()

    def detener(self) -> None:
        self._cliente.desconectar()
        if PuenteMQTT._activa is self:
            PuenteMQTT._activa = None
        super().detener()

    def ciclo(self) -> None:
        """paho tiene su propio hilo de red; aquí solo se vigila el enlace."""
        if not self._cliente.conectado():
            self.logger.warning("Sin conexión al broker MQTT (%s:%s); paho reintenta solo.",
                                config.mqtt.host, config.mqtt.puerto)
        time.sleep(CICLO_S)

    def _al_conectar(self) -> None:
        """Al (re)conectar se republica la configuración retenida de todos."""
        try:
            enviados = self._publicador.publicar_todos()
            self.logger.info("config retenida publicada para %d dispositivo(s).", enviados)
        finally:
            close_old_connections()

    # ------------------------------------------------------------------ #
    # Despacho
    # ------------------------------------------------------------------ #
    def procesar_mensaje(self, mensaje: MensajeMqtt) -> None:
        manejador = self._manejadores.get(mensaje.sufijo)
        if manejador is None:
            self.logger.debug("Tópico sin manejador: %s", mensaje.topico)
            return
        try:
            manejador(mensaje)
        except Exception:
            self.logger.exception("Error manejando el tópico %s", mensaje.topico)
        finally:
            # sync/resp no toca la BD; en los demás casos sí conviene cerrar.
            if mensaje.sufijo != "sync/req":
                close_old_connections()

    def _dispositivo_de(self, mensaje: MensajeMqtt):
        dispositivo = self._dispositivos.obtener(mensaje.codigo)
        if dispositivo is None:
            self._sin_dispositivo += 1
            self.logger.warning(
                "Mensaje MQTT de '%s' sin dispositivo registrado (tópico %s); se descarta. "
                "Registra el código en la tabla dispositivo.", mensaje.codigo, mensaje.topico,
            )
        return dispositivo

    @staticmethod
    def _validar(serializador, carga: dict):
        instancia = serializador(data=carga)
        if not instancia.is_valid():
            return None, instancia.errors
        return instancia.validated_data, None

    # ------------------------------------------------------------------ #
    # Manejadores por tópico
    # ------------------------------------------------------------------ #
    def _manejar_sync(self, mensaje: MensajeMqtt) -> None:
        """Se responde PRIMERO y sin tocar la BD: cada ms cuenta en el RTT."""
        datos, errores = self._validar(SyncPeticionSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("sync/req inválido de %s: %s", mensaje.codigo, errores)
            return
        self._cliente.publicar(
            mensaje.codigo, "sync/resp",
            {"id": datos["id"], "t0": datos["t0"], "server_ts_ms": mensaje.t_recepcion_ms},
            qos=0, retener=False,
        )

    def _manejar_telemetria(self, mensaje: MensajeMqtt) -> None:
        from monitoreo.services.telemetria_service import TelemetriaService

        datos, errores = self._validar(TelemetriaSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("telemetria inválida de %s: %s", mensaje.codigo, errores)
            return
        dispositivo = self._dispositivo_de(mensaje)
        if dispositivo is None:
            return
        TelemetriaService(self._estado_en_vivo).procesar(dispositivo, datos)

    def _manejar_gps(self, mensaje: MensajeMqtt) -> None:
        datos, errores = self._validar(GpsPosicionSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("gps inválido de %s: %s", mensaje.codigo, errores)
            return

        punto = self._estado_en_vivo.actualizar_gps(mensaje.codigo, dict(datos))
        if punto is None:
            # Sin fix (o descartada por HDOP): el panel lo muestra atenuado.
            NotificadorTiempoReal.gps(mensaje.codigo, {
                "fix": False, "sats": datos.get("sats"),
                "motivo": datos.get("motivo") or "", "modo": datos.get("modo") or "",
            })
            return

        NotificadorTiempoReal.gps(mensaje.codigo, punto.a_dict())
        if self._registro_gps is not None:
            self._registro_gps.registrar(mensaje.codigo, dict(datos))

    def _manejar_evento(self, mensaje: MensajeMqtt) -> None:
        from monitoreo.workers.procesador_eventos import ProcesadorEventos

        datos, errores = self._validar(EventoSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("evento inválido de %s: %s", mensaje.codigo, errores)
            return
        if self._dispositivo_de(mensaje) is None:
            return

        datos = dict(datos)
        datos["t_recepcion_ms"] = mensaje.t_recepcion_ms
        datos["origen"] = "MQTT"
        resultado = ProcesadorEventos.instancia().encolar(datos)

        if resultado.duplicado:
            self._eventos_duplicados += 1
            self.logger.info("Evento duplicado descartado: uid=%s (QoS 1 puede reentregar)",
                             datos.get("evento_uid"))
        elif not resultado.encolado:
            self.logger.error("Cola de eventos llena; se perdió uid=%s", datos.get("evento_uid"))
        else:
            self.logger.info("Evento %s encolado (uid=%s, nivel=%s)",
                             datos.get("tipo"), datos.get("evento_uid"), datos.get("nivel"))

    def _manejar_estado(self, mensaje: MensajeMqtt) -> None:
        """Tópico retenido + *Last Will*: conectividad inmediata (sección 5.3)."""
        from monitoreo.models import EstadoDispositivo

        datos, errores = self._validar(EstadoNodoSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("estado inválido de %s: %s", mensaje.codigo, errores)
            return
        dispositivo = self._dispositivo_de(mensaje)
        if dispositivo is None:
            return

        en_linea = bool(datos["en_linea"])
        self._estado_en_vivo.actualizar_estado_mqtt(mensaje.codigo, dict(datos))

        campos = []
        if en_linea:
            dispositivo.ultimo_contacto = timezone.now()
            campos.append("ultimo_contacto")
            if datos.get("ip"):
                dispositivo.ip = datos["ip"]
                campos.append("ip")
            if datos.get("mac"):
                dispositivo.mac_esp32 = datos["mac"]
                campos.append("mac_esp32")
            if datos.get("firmware"):
                dispositivo.version_firmware = datos["firmware"]
                campos.append("version_firmware")
        if campos:
            dispositivo.save(update_fields=campos)
            self._dispositivos.invalidar(mensaje.codigo)

        EstadoDispositivo.objects.create(
            dispositivo=dispositivo, en_linea=en_linea, rssi_wifi_dbm=datos.get("rssi_dbm"),
        )
        NotificadorTiempoReal.dispositivo_estado({
            "codigo": mensaje.codigo, "en_linea": en_linea,
            "armado": datos.get("armado", dispositivo.armado),
            "modo": datos.get("modo") or self._estado_en_vivo.modo(mensaje.codigo),
            "firmware": datos.get("firmware") or dispositivo.version_firmware,
            "ultimo_contacto": (
                dispositivo.ultimo_contacto.isoformat() if dispositivo.ultimo_contacto else None
            ),
        })
        self.logger.info("Nodo %s %s (por tópico 'estado'%s)", mensaje.codigo,
                         "EN LÍNEA" if en_linea else "FUERA DE LÍNEA",
                         " retenido" if mensaje.retenido else "")

        # Al verlo aparecer se le reenvía su configuración vigente.
        if en_linea:
            self._publicador.publicar(dispositivo)

    def _manejar_modo(self, mensaje: MensajeMqtt) -> None:
        datos, errores = self._validar(ModoSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("modo inválido de %s: %s", mensaje.codigo, errores)
            return

        self._estado_en_vivo.actualizar_modo(
            mensaje.codigo, datos["modo"], datos.get("fuente") or "", datos.get("contacto"),
        )
        NotificadorTiempoReal.dispositivo_estado({
            "codigo": mensaje.codigo, "modo": datos["modo"],
            "modo_fuente": datos.get("fuente") or "", "contacto": datos.get("contacto"),
        })
        NotificadorTiempoReal.modo(mensaje.codigo, dict(datos))
        self.logger.info("Modo de %s: %s (fuente %s)", mensaje.codigo, datos["modo"],
                         datos.get("fuente") or "—")

    def _manejar_camara_estado(self, mensaje: MensajeMqtt) -> None:
        datos, errores = self._validar(EstadoCamaraSerializer, mensaje.carga)
        if datos is None:
            self.logger.warning("cam/estado inválido de %s: %s", mensaje.codigo, errores)
            return

        estado = dict(datos)
        # El panel lee "ok" para pintar la tarjeta de la cámara; el Last Will
        # solo trae en_linea=false, así que se deriva.
        estado.setdefault("ok", estado.get("en_linea", False))
        if not estado.get("en_linea"):
            estado["ok"] = False
        self._estado_en_vivo.actualizar_estado_camara(mensaje.codigo, estado)
        NotificadorTiempoReal.camara_estado(mensaje.codigo, estado)

        mac = estado.get("mac")
        if not mac:
            return
        dispositivo = self._dispositivo_de(mensaje)
        if dispositivo is not None and dispositivo.mac_esp32cam != mac:
            dispositivo.mac_esp32cam = mac
            dispositivo.save(update_fields=["mac_esp32cam"])
            self._dispositivos.invalidar(mensaje.codigo)
