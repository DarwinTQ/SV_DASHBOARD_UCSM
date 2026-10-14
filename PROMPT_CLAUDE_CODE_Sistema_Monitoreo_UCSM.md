# PROMPT PARA CLAUDE CODE — Sistema de Monitoreo Vehicular Inteligente (YOLO + IoT) · UCSM


## 0. Instrucciones de trabajo para Claude Code

Eres un ingeniero de software senior. Vas a construir, **dentro del proyecto Django que ya existe en esta carpeta** (está creado pero vacío), el sistema completo descrito abajo. Reglas:

1. **Primero inspecciona** el proyecto: localiza `manage.py`, el paquete de configuración (el que contiene `settings.py`, `urls.py`, `asgi.py`) y la versión de Django instalada (`python -m django --version`). **No recrees el proyecto** ni cambies su nombre; adapta las rutas de este documento al nombre real (aquí lo llamo `<proyecto>`).
2. Trabaja **por fases** (sección 15). Al terminar cada fase ejecuta `python manage.py check`, las migraciones y los tests de esa fase. No pases a la siguiente fase con errores.
3. **Programación orientada a objetos en todo el backend**: cada responsabilidad es una clase (servicios, workers, clientes HTTP, configuración, servidor principal). Nada de lógica de negocio suelta en funciones de vista.
4. **Arquitectura MVC** explícita (sección 3): `models/` = Modelo, `templates/` = Vista, `controllers/` = Controlador (son las *class-based views* de Django), más una capa `services/` con la lógica de negocio.
5. **La base de datos de la sección 6 es obligatoria y fija**: mismos modelos, mismos campos, mismos `db_table`. Puedes añadir métodos, propiedades, `Meta.indexes` y managers, **pero no agregar, quitar ni renombrar campos/tablas**.
6. El **contrato de la API** (sección 7) es fijo porque el firmware ya está programado con él. No cambies rutas, nombres de campos JSON ni cabeceras.
7. Todo el código, comentarios, nombres de clases/métodos y textos de la interfaz **en español** (excepto palabras reservadas y nombres de librerías).
8. Todos los secretos (tokens, API keys) se leen de `.env` a través de `config.py`. **Nunca** los escribas en el código ni en los templates.
9. El sistema corre **localmente en Windows** (PC del estudiante en la misma red Wi-Fi que los ESP32). Usa rutas con `pathlib`, nada específico de Linux.
10. Al final entrega un `README.md` con instalación, ejecución, credenciales iniciales y cómo probar sin hardware.

---

## 1. Contexto del sistema (tesis)

Sistema IoT para vehículos particulares que detecta eventos de riesgo (golpes, ruidos fuertes, manipulación, movimiento no autorizado y **presencia de personas detectadas por YOLO**), guarda cada incidencia con su **imagen** en una base de datos **SQLite local**, envía la **alerta al propietario por Telegram** y **mide los tiempos** de cada etapa (latencia, inferencia, BD, Telegram, tiempo de respuesta total) para el Capítulo V de la tesis.

```
 ┌───────────────────────┐   POST JSON /api/v1/telemetria/ (cada 1 s)    ┌────────────────────────────────────────────┐
 │ NODO ESP32-S3         │ ─────────────────────────────────────────────▶ │  SERVIDOR LOCAL  (python main.py)          │
 │ GPS · MPU6050 ·       │   POST JSON /api/v1/eventos/   (inmediato)     │  Django + Channels (Daphne, ASGI)          │
 │ INMP441 · batería     │ ◀───────────── {server_ts_ms, armado, umbrales}│                                            │
 └───────────────────────┘                                                │  ├─ API REST (DRF)                         │
                                                                          │  ├─ ProcesadorEventos (hilo)               │
 ┌───────────────────────┐   GET /capture  (JPEG)  · GET /status (JSON)   │  │    captura → YOLO → BD → Telegram       │
 │ ESP32-CAM (OV2640)    │ ◀───────────────────────────────────────────── │  ├─ VigilanciaVisual (hilo, YOLO continuo) │
 │ servidor HTTP en LAN  │                                                │  ├─ MonitorDispositivos (hilo, online/off) │
 └───────────────────────┘                                                │  ├─ SQLite (db.sqlite3) + media/ (fotos)   │
                                                                          │  └─ Web responsive + WebSocket (tiempo real)│
 ┌───────────────────────┐   sendPhoto (HTTPS Bot API)                    └──────────────────────┬─────────────────────┘
 │ Telegram (propietario)│ ◀─────────────────────────────────────────────────────────────────────┘
 └───────────────────────┘
```

---

## 2. Stack y dependencias

- Python 3.11+ · Django (la versión ya instalada, mínimo 4.2) · **SQLite**.
- `requirements.txt` (créalo/actualízalo):
  ```
  Django>=4.2
  djangorestframework
  channels>=4
  daphne>=4
  whitenoise
  python-dotenv
  requests
  numpy
  opencv-python-headless
  pillow
  ultralytics
  openpyxl
  ```
- Frontend: plantillas Django + **Bootstrap 5.3**, **Bootstrap Icons**, **Chart.js 4**, **Leaflet 1.9** (respaldo si no hay clave de Google Maps). **Descarga una sola vez** los archivos `.min.css/.min.js` (y las fuentes de Bootstrap Icons) a `static/vendor/` para que la web funcione en la LAN sin depender de un CDN. Google Maps JS solo se carga si hay `GOOGLE_MAPS_API_KEY`.
- Tiempo real: **Django Channels** con `InMemoryChannelLayer` (un solo proceso; no necesita Redis) servido por **Daphne**.

---

## 3. Estructura de carpetas (MVC)

```
<raíz del proyecto>/
├── main.py                      # Clase ServidorPrincipal: arranca TODO (sección 5)
├── config.py                    # Clase Config: TODA la configuración (sección 4)
├── .env                         # secretos reales (NO subir a git)
├── .env.example                 # plantilla documentada
├── requirements.txt
├── README.md
├── manage.py
├── <proyecto>/                  # paquete existente
│   ├── settings.py              # lee todo desde config.py
│   ├── urls.py                  # incluye monitoreo.urls y monitoreo.api_urls
│   ├── asgi.py                  # ProtocolTypeRouter (http + websocket)
│   └── wsgi.py
├── monitoreo/                   # app principal
│   ├── apps.py
│   ├── admin.py
│   ├── models/                  # ── M: MODELO ─────────────────────────────
│   │   ├── __init__.py          #   exporta todos los modelos
│   │   ├── conductor.py         #   Conductor
│   │   ├── vehiculo.py          #   Vehiculo
│   │   ├── dispositivo.py       #   Dispositivo, EstadoDispositivo
│   │   ├── evento.py            #   TipoEvento
│   │   ├── incidencia.py        #   Incidencia, DeteccionYolo
│   │   ├── medicion.py          #   MedicionTiempo
│   │   └── alerta.py            #   AlertaTelegram
│   ├── repositories/            # consultas ORM reutilizables (una clase por agregado)
│   │   ├── incidencia_repository.py
│   │   ├── dispositivo_repository.py
│   │   └── metricas_repository.py
│   ├── services/                # ── lógica de negocio (clases) ────────────
│   │   ├── reloj_service.py             # RelojServidor: ahora_ms(), ms→datetime
│   │   ├── estado_en_vivo.py            # EstadoEnVivo (singleton thread-safe en memoria)
│   │   ├── notificador_tiempo_real.py   # NotificadorTiempoReal (group_send a WebSocket)
│   │   ├── telemetria_service.py        # TelemetriaService
│   │   ├── camara_service.py            # ClienteCamaraESP32 / CamaraSimulada
│   │   ├── detector_yolo.py             # DetectorYOLO (singleton, lock, warm-up)
│   │   ├── incidencia_service.py        # IncidenciaService (pipeline completo)
│   │   ├── telegram_service.py          # TelegramService
│   │   ├── mapas_service.py             # MapasService (Google Maps / OSM)
│   │   ├── metricas_service.py          # MetricasService (resultados de tesis)
│   │   ├── disponibilidad_service.py    # DisponibilidadService
│   │   └── exportacion_service.py       # ExportacionService (Excel/CSV)
│   ├── workers/                 # hilos de fondo (los arranca main.py)
│   │   ├── base_worker.py               # WorkerBase (start/stop/loop, logging)
│   │   ├── procesador_eventos.py        # ProcesadorEventos (cola de eventos)
│   │   ├── vigilancia_visual.py         # VigilanciaVisual (YOLO continuo)
│   │   └── monitor_dispositivos.py      # MonitorDispositivos (online/offline)
│   ├── controllers/             # ── C: CONTROLADOR (class-based views) ───
│   │   ├── web/
│   │   │   ├── auth_controller.py
│   │   │   ├── panel_controller.py
│   │   │   ├── incidencia_controller.py
│   │   │   ├── conductor_controller.py
│   │   │   ├── vehiculo_controller.py
│   │   │   ├── dispositivo_controller.py
│   │   │   ├── tipo_evento_controller.py
│   │   │   ├── alerta_controller.py
│   │   │   ├── resultados_controller.py
│   │   │   ├── configuracion_controller.py
│   │   │   └── media_controller.py      # sirve /media/ solo a usuarios logueados
│   │   └── api/
│   │       ├── telemetria_api.py        # POST /api/v1/telemetria/
│   │       ├── eventos_api.py           # POST /api/v1/eventos/
│   │       └── panel_api.py             # JSON para el frontend (sesión)
│   ├── serializers/             # DRF: validación de los JSON del firmware
│   ├── forms/                   # ModelForms con validación y estilos
│   ├── security/
│   │   ├── api_key.py                   # AutenticacionApiKey (DRF)
│   │   └── middleware.py                # MarcaTiempoRecepcionMiddleware
│   ├── consumers/
│   │   └── panel_consumer.py            # PanelConsumer (AsyncJsonWebsocketConsumer)
│   ├── routing.py               # ws/panel/
│   ├── urls.py                  # rutas web
│   ├── api_urls.py              # rutas /api/v1/
│   ├── management/commands/
│   │   ├── sembrar_catalogos.py         # tipos de evento + superusuario demo
│   │   ├── simular_nodo.py              # emula el ESP32-S3 (sección 11)
│   │   ├── telegram_chat_ids.py         # lista chat_id con getUpdates
│   │   └── exportar_resultados.py       # Excel de resultados
│   ├── templates/monitoreo/     # ── V: VISTA ──────────────────────────────
│   │   ├── base.html                    # layout: topbar + sidebar + fondo
│   │   ├── componentes/                 # _sidebar, _topbar, _kpi, _tabla, _paginacion, _toast, _badge_riesgo
│   │   ├── auth/login.html
│   │   ├── panel/panel.html
│   │   ├── incidencias/ (lista, detalle, mapa)
│   │   ├── conductores/ vehiculos/ dispositivos/ tipos_evento/ (lista, form, detalle, confirmar_borrar)
│   │   ├── alertas/lista.html
│   │   ├── resultados/resultados.html
│   │   └── configuracion/configuracion.html
│   ├── static/monitoreo/
│   │   ├── css/app.css                  # sistema de diseño (sección 9)
│   │   ├── js/ (app.js, panel.js, resultados.js, mapa.js, ws.js)
│   │   └── img/ (logo_ucsm_letra.png, logo_ucsm_fondo.png, favicon.png)
│   └── tests/
├── static/vendor/               # bootstrap, bootstrap-icons, chart.js, leaflet
├── media/                       # capturas/AAAA/MM, anotadas/AAAA/MM, live/, simulacion/
├── modelos_ia/                  # pesos YOLO (yolo26n.pt o best.pt entrenado)
├── logs/                        # sistema.log (rotativo)
├── assets/                      # logos originales del usuario (ya existen)
└── firmware/
    ├── nodo_iot_esp32s3/nodo_iot_esp32s3.ino
    └── esp32cam_camara/esp32cam_camara.ino
```

**Logos:** copia `assets/LOGO_letra_UCSM.png` → `monitoreo/static/monitoreo/img/logo_ucsm_letra.png` y `assets/solo_logo_ucsm_PARA FONDO_PAG.png` → `monitoreo/static/monitoreo/img/logo_ucsm_fondo.png` (el original tiene espacios en el nombre; renómbralo al copiar). Genera `favicon.png` (64×64) a partir del logo de fondo con Pillow.

---

## 4. `config.py` — configuración centralizada (POO)

Crea `config.py` en la raíz con **dataclasses inmutables agrupadas** y una clase `Config` con método de clase `cargar()` que lee `.env` (python-dotenv) con valores por defecto, valida tipos/rangos y expone una instancia única `config`. `settings.py`, los servicios y `main.py` importan **solo** desde aquí. Incluye un método `resumen_seguro()` que devuelve un dict con los secretos enmascarados (`abcd…wxyz`) para la página de Configuración.

Grupos y claves mínimas (nombre de variable `.env` → valor por defecto):

```python
@dataclass(frozen=True)
class ServidorConfig:
    host: str            # SERVIDOR_HOST = "0.0.0.0"
    puerto: int          # SERVIDOR_PUERTO = 8000
    debug: bool          # DJANGO_DEBUG = True
    secret_key: str      # DJANGO_SECRET_KEY (generar si falta y avisar)
    allowed_hosts: list  # DJANGO_ALLOWED_HOSTS = "*"
    url_publica: str     # URL_PUBLICA = ""  (ej. http://192.168.1.10:8000, para links en Telegram)
    zona_horaria: str    # ZONA_HORARIA = "America/Lima"

@dataclass(frozen=True)
class BaseDatosConfig:
    ruta_sqlite: Path    # SQLITE_RUTA = BASE_DIR / "db.sqlite3"
    media_root: Path     # MEDIA_ROOT = BASE_DIR / "media"

@dataclass(frozen=True)
class Esp32Config:      # nodo ESP32-S3 (cliente que llama a nuestra API)
    api_key: str                      # ESP32_API_KEY = "tesis-ucsm-2026"
    ruta_telemetria: str              # "/api/v1/telemetria/"
    ruta_eventos: str                 # "/api/v1/eventos/"
    timeout_offline_s: int            # DISPOSITIVO_TIMEOUT_S = 10
    intervalo_estado_s: int           # ESTADO_INTERVALO_S = 30  (cada cuánto se guarda estado_dispositivo)

@dataclass(frozen=True)
class Esp32CamConfig:   # ESP32-CAM (servidor HTTP al que llamamos)
    urls_por_dispositivo: dict        # ESP32CAM_URLS = "NODO-01=http://10.144.55.120"
    api_key: str                      # ESP32CAM_API_KEY = "tesis-ucsm-2026"
    timeout_s: float                  # ESP32CAM_TIMEOUT_S = 4
    usar_flash_noche: bool            # ESP32CAM_FLASH = False
    simulada: bool                    # ESP32CAM_SIMULADA = False (usa media/simulacion/*.jpg)
    intervalo_status_s: int           # ESP32CAM_STATUS_S = 30

@dataclass(frozen=True)
class YoloConfig:
    ruta_modelo: Path        # YOLO_MODELO = BASE_DIR / "modelos_ia" / "yolo26n.pt"
    nombre_modelo: str       # YOLO_NOMBRE = "yolo26n"
    confianza_minima: float  # YOLO_CONFIANZA = 0.50   (cajas que se guardan)
    confianza_alerta: float  # YOLO_CONFIANZA_ALERTA = 0.60 (para declarar PERSONA)
    clases_objetivo: tuple   # YOLO_CLASES = "person"
    tamano_imagen: int       # YOLO_IMGSZ = 640
    dispositivo: str         # YOLO_DEVICE = "cpu" (o "0" si hay GPU CUDA)

@dataclass(frozen=True)
class VisionContinuaConfig:
    activa: bool               # VISION_CONTINUA = True
    intervalo_ms: int          # VISION_INTERVALO_MS = 500
    frames_confirmacion: int   # VISION_FRAMES_CONFIRMACION = 2
    cooldown_s: int            # VISION_COOLDOWN_S = 30

@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str           # TELEGRAM_BOT_TOKEN
    api_base: str            # "https://api.telegram.org"
    timeout_s: float         # TELEGRAM_TIMEOUT_S = 10
    reintentos: int          # TELEGRAM_REINTENTOS = 3
    enviar_sin_persona: bool # TELEGRAM_ALERTAR_SIN_PERSONA = True (alertar aunque YOLO no vea personas)
    habilitado: bool         # TELEGRAM_HABILITADO = True (False en pruebas/simulación)

@dataclass(frozen=True)
class GoogleMapsConfig:
    api_key: str             # GOOGLE_MAPS_API_KEY = ""  (vacío → Leaflet/OpenStreetMap)
    geocodificar: bool       # GOOGLE_GEOCODING = False (dirección aproximada, DESPUÉS de alertar)
    zoom: int                # MAPA_ZOOM = 16

@dataclass(frozen=True)
class UmbralesConfig:        # se envían al ESP32-S3 en cada respuesta
    impacto_g: float         # UMBRAL_IMPACTO_G = 0.35
    ruido_db: float          # UMBRAL_RUIDO_DB = 85
    inclinacion_deg: float   # UMBRAL_INCLINACION_DEG = 3
    giro_dps: float          # UMBRAL_GIRO_DPS = 60
    movimiento_kmh: float    # UMBRAL_MOVIMIENTO_KMH = 8
    cooldown_s: int          # UMBRAL_COOLDOWN_S = 15

@dataclass(frozen=True)
class ProcesamientoConfig:
    hilos_eventos: int       # EVENTOS_HILOS = 1
    tamano_cola: int         # EVENTOS_COLA = 50

class Config:
    BASE_DIR: Path
    servidor: ServidorConfig; bd: BaseDatosConfig; esp32: Esp32Config; esp32cam: Esp32CamConfig
    yolo: YoloConfig; vision: VisionContinuaConfig; telegram: TelegramConfig
    maps: GoogleMapsConfig; umbrales: UmbralesConfig; procesamiento: ProcesamientoConfig
    @classmethod
    def cargar(cls) -> "Config": ...
    def url_camara(self, codigo_dispositivo: str) -> str | None: ...
    def resumen_seguro(self) -> dict: ...

config = Config.cargar()
```

Crea también `.env.example` con **todas** las variables, comentadas en español, y un `.env` inicial con los mismos valores por defecto (token de Telegram vacío).

---

## 5. `main.py` — servidor principal (POO)

Clase `ServidorPrincipal` que es **el único punto de arranque** (`python main.py`). Responsabilidades, en este orden:

1. Configurar logging (consola + `logs/sistema.log` rotativo 5 MB × 5).
2. `django.setup()` con `DJANGO_SETTINGS_MODULE` del proyecto.
3. Verificar entorno: carpetas `media/`, `logs/`, `modelos_ia/`; si falta el peso YOLO, descargar `yolo26n.pt` con ultralytics (si falla, avisar y seguir sin visión).
4. `migrate` automático y `sembrar_catalogos` (idempotente).
5. Activar SQLite en **modo WAL** (`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` mediante la señal `connection_created`) porque escriben varios hilos a la vez.
6. Cargar `DetectorYOLO` una vez y hacer **warm-up** (inferencia sobre imagen negra) para que la primera alerta no pague la carga del modelo.
7. Arrancar los workers: `ProcesadorEventos`, `VigilanciaVisual` (si `config.vision.activa`) y `MonitorDispositivos`.
8. Imprimir un banner con: URL local, IP de la LAN (para poner en el firmware), URL de la cámara, estado de Telegram/Google Maps.
9. Levantar **Daphne** programáticamente (`daphne.server.Server` + `build_endpoint_description_strings(host, puerto)`) sirviendo `<proyecto>.asgi:application`.
10. Apagado limpio con Ctrl+C: detener workers (evento `threading.Event`), vaciar cola, cerrar conexiones.

Argumentos CLI (`argparse`): `--host`, `--puerto`, `--sin-vision`, `--simular` (cámara simulada + Telegram deshabilitado), `--solo-migrar`.

**Importante:** los workers **solo** los arranca `main.py` (nunca en `AppConfig.ready()`), así `manage.py` y los tests no los levantan.

---

## 6. Base de datos (FIJA) — modelos Django

Divide estos modelos en el paquete `monitoreo/models/` (un archivo por agregado, ver sección 3) y expórtalos en `__init__.py`. **Campos, tipos y `db_table` exactamente así**:

```python
from django.core.validators import RegexValidator, MinValueValidator, MaxValueValidator
from django.db import models


class Conductor(models.Model):
    dni = models.CharField("DNI", max_length=8, unique=True,
                           validators=[RegexValidator(r"^\d{8}$", "El DNI debe tener 8 dígitos")])
    nombres = models.CharField(max_length=80)
    apellidos = models.CharField(max_length=80)
    telefono = models.CharField("Teléfono", max_length=15,
                                validators=[RegexValidator(r"^\+?\d{9,15}$", "Teléfono inválido")])
    correo = models.EmailField(max_length=100, blank=True)
    telegram_chat_id = models.CharField("Chat ID de Telegram", max_length=30, blank=True,
                                        help_text="Se obtiene cuando el conductor escribe /start al bot")
    direccion = models.CharField("Dirección", max_length=150, blank=True)
    fecha_registro = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "conductor"
        verbose_name_plural = "conductores"
        ordering = ["apellidos", "nombres"]

    def __str__(self):
        return f"{self.apellidos}, {self.nombres} ({self.dni})"


class Vehiculo(models.Model):
    TIPOS = [("SEDAN", "Sedán"), ("HATCHBACK", "Hatchback"), ("SUV", "SUV"),
             ("PICKUP", "Pick-up"), ("VAN", "Van"), ("OTRO", "Otro")]
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT,
                                  related_name="vehiculos", db_column="id_conductor")
    placa = models.CharField(max_length=10, unique=True,
                             validators=[RegexValidator(r"^[A-Z0-9]{3}-?[A-Z0-9]{3}$", "Formato de placa: ABC-123")])
    marca = models.CharField(max_length=40)
    modelo = models.CharField(max_length=40)
    anio = models.PositiveSmallIntegerField("Año", validators=[MinValueValidator(1980), MaxValueValidator(2100)])
    color = models.CharField(max_length=30)
    tipo = models.CharField(max_length=20, choices=TIPOS, default="SEDAN")
    activo = models.BooleanField(default=True)

    class Meta:
        db_table = "vehiculo"

    def save(self, *args, **kwargs):
        self.placa = self.placa.upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.placa} — {self.marca} {self.modelo} ({self.color})"


class Dispositivo(models.Model):
    ESTADOS = [("ACTIVO", "Activo"), ("INACTIVO", "Inactivo"), ("MANTENIMIENTO", "Mantenimiento")]
    vehiculo = models.OneToOneField(Vehiculo, on_delete=models.PROTECT,
                                    related_name="dispositivo", db_column="id_vehiculo")
    codigo = models.CharField(max_length=20, unique=True, help_text="Ej.: NODO-01")
    mac_esp32 = models.CharField("MAC ESP32", max_length=17, blank=True)
    mac_esp32cam = models.CharField("MAC ESP32-CAM", max_length=17, blank=True)
    ip = models.GenericIPAddressField(protocol="IPv4", null=True, blank=True)
    version_firmware = models.CharField(max_length=10, blank=True)
    estado = models.CharField(max_length=15, choices=ESTADOS, default="ACTIVO")
    ultimo_contacto = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "dispositivo"

    def __str__(self):
        return f"{self.codigo} ({self.vehiculo.placa})"


class EstadoDispositivo(models.Model):
    """Heartbeat periódico -> DISPONIBILIDAD del sistema."""
    dispositivo = models.ForeignKey(Dispositivo, on_delete=models.CASCADE,
                                    related_name="estados", db_column="id_dispositivo")
    fecha_hora = models.DateTimeField(auto_now_add=True, db_index=True)
    en_linea = models.BooleanField(default=True)
    rssi_wifi_dbm = models.SmallIntegerField("RSSI Wi-Fi (dBm)", null=True, blank=True)
    voltaje_v = models.FloatField("Voltaje (V)", null=True, blank=True)
    temperatura_c = models.FloatField("Temperatura (°C)", null=True, blank=True)
    ram_libre_kb = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        db_table = "estado_dispositivo"
        ordering = ["-fecha_hora"]


class TipoEvento(models.Model):
    RIESGO = [("BAJO", "Bajo"), ("MEDIO", "Medio"), ("ALTO", "Alto")]
    codigo = models.CharField(max_length=20, unique=True)
    nombre = models.CharField(max_length=60)
    nivel_riesgo = models.CharField(max_length=10, choices=RIESGO, default="MEDIO")
    descripcion = models.TextField(blank=True)

    class Meta:
        db_table = "tipo_evento"

    def __str__(self):
        return self.nombre


class Incidencia(models.Model):
    ESTADOS = [("PENDIENTE", "Pendiente"), ("NOTIFICADA", "Notificada"), ("ATENDIDA", "Atendida")]
    VALIDACION = [("VP", "Verdadero positivo"), ("FP", "Falso positivo"),
                  ("FN", "Falso negativo"), ("VN", "Verdadero negativo")]

    dispositivo = models.ForeignKey(Dispositivo, on_delete=models.PROTECT,
                                    related_name="incidencias", db_column="id_dispositivo")
    tipo = models.ForeignKey(TipoEvento, on_delete=models.PROTECT,
                             related_name="incidencias", db_column="id_tipo")
    fecha_hora = models.DateTimeField(db_index=True)
    aceleracion_g = models.FloatField("|a| (g)", null=True, blank=True)
    nivel_audio_db = models.FloatField("Nivel de audio (dB)", null=True, blank=True)
    latitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitud = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    imagen = models.ImageField(upload_to="capturas/%Y/%m/", null=True, blank=True)
    imagen_anotada = models.ImageField(upload_to="anotadas/%Y/%m/", null=True, blank=True,
                                       help_text="Imagen con las cajas de YOLO dibujadas")
    estado = models.CharField(max_length=15, choices=ESTADOS, default="PENDIENTE")
    validacion = models.CharField(max_length=2, choices=VALIDACION, null=True, blank=True)
    observacion = models.TextField(blank=True)

    class Meta:
        db_table = "incidencia"
        ordering = ["-fecha_hora"]

    def __str__(self):
        return f"#{self.pk} {self.tipo} — {self.dispositivo.vehiculo.placa} — {self.fecha_hora:%d/%m/%Y %H:%M:%S}"

    @property
    def link_mapa(self):
        if self.latitud is None:
            return ""
        return f"https://maps.google.com/?q={self.latitud},{self.longitud}"


class DeteccionYolo(models.Model):
    incidencia = models.ForeignKey(Incidencia, on_delete=models.CASCADE,
                                   related_name="detecciones", db_column="id_incidencia")
    clase = models.CharField(max_length=30)
    confianza = models.FloatField(validators=[MinValueValidator(0), MaxValueValidator(1)])
    x1 = models.IntegerField()
    y1 = models.IntegerField()
    x2 = models.IntegerField()
    y2 = models.IntegerField()
    modelo_yolo = models.CharField(max_length=30, default="yolo26n")

    class Meta:
        db_table = "deteccion_yolo"


class MedicionTiempo(models.Model):
    incidencia = models.OneToOneField(Incidencia, on_delete=models.CASCADE,
                                      related_name="tiempos", db_column="id_incidencia")
    t_deteccion_sensor = models.DateTimeField(help_text="Cuando el sensor detecta el evento")
    t_recepcion_servidor = models.DateTimeField(help_text="Cuando Django recibe la petición")
    t_inicio_inferencia = models.DateTimeField(null=True, blank=True)
    t_fin_inferencia = models.DateTimeField(null=True, blank=True)
    t_registro_bd = models.DateTimeField(null=True, blank=True)
    t_envio_telegram = models.DateTimeField(null=True, blank=True)
    t_confirmacion_telegram = models.DateTimeField(null=True, blank=True)
    latencia_red_ms = models.FloatField(null=True, blank=True)
    tiempo_inferencia_ms = models.FloatField(null=True, blank=True)
    tiempo_bd_ms = models.FloatField(null=True, blank=True)
    latencia_telegram_ms = models.FloatField(null=True, blank=True)
    tiempo_respuesta_total_ms = models.FloatField(null=True, blank=True)
    fps = models.FloatField(null=True, blank=True)

    class Meta:
        db_table = "medicion_tiempo"

    @staticmethod
    def _ms(a, b):
        return round((b - a).total_seconds() * 1000, 2) if a and b else None

    def save(self, *args, **kwargs):
        self.latencia_red_ms = self._ms(self.t_deteccion_sensor, self.t_recepcion_servidor)
        self.tiempo_inferencia_ms = self._ms(self.t_inicio_inferencia, self.t_fin_inferencia)
        self.tiempo_bd_ms = self._ms(self.t_fin_inferencia, self.t_registro_bd)
        self.latencia_telegram_ms = self._ms(self.t_envio_telegram, self.t_confirmacion_telegram)
        self.tiempo_respuesta_total_ms = self._ms(self.t_deteccion_sensor, self.t_confirmacion_telegram)
        self.fps = round(1000 / self.tiempo_inferencia_ms, 2) if self.tiempo_inferencia_ms else None
        super().save(*args, **kwargs)


class AlertaTelegram(models.Model):
    ESTADOS = [("ENVIADO", "Enviado"), ("FALLIDO", "Fallido"), ("PENDIENTE", "Pendiente")]
    incidencia = models.ForeignKey(Incidencia, on_delete=models.CASCADE,
                                   related_name="alertas", db_column="id_incidencia")
    conductor = models.ForeignKey(Conductor, on_delete=models.PROTECT,
                                  related_name="alertas", db_column="id_conductor")
    chat_id = models.CharField(max_length=30)
    message_id = models.BigIntegerField(null=True, blank=True)
    mensaje = models.TextField()
    con_imagen = models.BooleanField(default=True)
    fecha_envio = models.DateTimeField(null=True, blank=True)
    estado_envio = models.CharField(max_length=10, choices=ESTADOS, default="PENDIENTE")
    codigo_http = models.PositiveSmallIntegerField(null=True, blank=True)
    intentos = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True)

    class Meta:
        db_table = "alerta_telegram"
        ordering = ["-fecha_envio"]
```

**Ajuste permitido (sin cambiar campos):** en `MedicionTiempo.save()` respeta un atributo de instancia no persistido `self._sin_reloj_sensor = True` → en ese caso `latencia_red_ms = None` y `tiempo_respuesta_total_ms` se calcula desde `t_recepcion_servidor` (se usa cuando el ESP32 envía `ts_ms: null`). Anótalo también en `incidencia.observacion`.

**Catálogo inicial `tipo_evento`** (comando `sembrar_catalogos`, idempotente con `update_or_create`):

| codigo | nombre | nivel_riesgo | descripcion |
|---|---|---|---|
| PERSONA | Persona detectada (YOLO) | ALTO | YOLO detectó una persona junto al vehículo |
| VIBRACION | Golpe o vibración | MEDIO | Impacto detectado por el MPU-6050 |
| RUIDO | Ruido fuerte | MEDIO | Nivel sonoro sobre el umbral (INMP441) |
| MANIPULACION | Manipulación / inclinación | ALTO | Cambio de inclinación o giro brusco (gata, remolque) |
| MOVIMIENTO | Movimiento no autorizado | ALTO | El GPS reporta desplazamiento estando armado |

Agrega `Meta.indexes` útiles (por ejemplo `incidencia(dispositivo, fecha_hora)`, `estado_dispositivo(dispositivo, fecha_hora)`) mediante migración; eso no altera campos.

---

## 7. Contrato de la API (FIJO — el firmware ya lo usa)

### 7.1 Seguridad común
- Cabecera `X-API-Key` comparada con `hmac.compare_digest` contra `config.esp32.api_key` → si falla: **401** `{"ok": false, "error": "API key inválida"}`.
- Cabecera informativa `X-Device-Code` (debe coincidir con `codigo` del body).
- `codigo` inexistente en la tabla `dispositivo` → **404** `{"ok": false, "error": "dispositivo no registrado"}`.
- JSON inválido → **400** con los errores del serializer.
- Endpoints API exentos de CSRF; DRF con `AutenticacionApiKey` propia.
- **`MarcaTiempoRecepcionMiddleware`**: la PRIMERA cosa que hace cada request es guardar `request.t_recepcion_ms = time.time_ns() // 1_000_000`. Ese valor es `t_recepcion_servidor` y también el `server_ts_ms` de la respuesta (el firmware lo usa para sincronizar su reloj; **debe** ser el instante de recepción, no el de respuesta).

### 7.2 `POST /api/v1/telemetria/` (cada 1 s)
Body real generado por el firmware:
```json
{"codigo":"NODO-01","firmware":"2.0.0","mac":"24:6F:28:AA:BB:CC","ip":"10.144.55.50",
 "boot_id":2882343476,"seq":1,"uptime_ms":6600,"ts_ms":1791867074163,"ts_fuente":"SERVIDOR","armado":true,
 "wifi":{"rssi_dbm":-58},"sistema":{"ram_libre_kb":250,"temp_c":33.1},
 "gps":{"fix":true,"sats":7,"lat":-16.398912,"lon":-71.536987,"vel_kmh":0.4,"alt_m":2335.2,"hdop":1.1},
 "imu":{"ax":0,"ay":0,"az":1,"a_max_g":1.05,"vib_rms_g":0.0223,"vib_pp_g":0.07,"giro_max_dps":0,
        "pitch_deg":0,"roll_deg":0,"muestras":200},
 "audio":{"rms_dbfs":-72.45,"spl_db":47.55,"pico_dbfs":-48.93,"pico_spl_db":71.07,"muestras":16000},
 "bateria":{"v":3.9,"pct":65},
 "http":{"rtt_prev_ms":12,"ok":10,"fallos":0,"descartados":0}}
```
Notas: `gps` sin fix llega como `{"fix":false,"sats":0}` (sin lat/lon); `imu`, `audio` o `bateria` pueden ser `null`; `ts_ms` puede ser `null` con `ts_fuente:"NINGUNA"`; `ts_fuente` ∈ {`SERVIDOR`,`NTP`,`NINGUNA`}.

Respuesta **200**:
```json
{"ok":true,"server_ts_ms":1760330000123,"armado":true,
 "umbrales":{"impacto_g":0.35,"ruido_db":85,"inclinacion_deg":3,"giro_dps":60,"movimiento_kmh":8,"cooldown_s":15}}
```
`armado = (dispositivo.estado == "ACTIVO")`. `umbrales` = `config.umbrales`.

Lógica (`TelemetriaService.procesar`):
1. Actualizar `dispositivo`: `ip`, `mac_esp32`, `version_firmware`, `ultimo_contacto` (con `update_fields`).
2. Guardar la lectura completa en `EstadoEnVivo` (memoria; incluye último GPS válido, historial circular de 120 s para las gráficas en vivo).
3. Insertar `estado_dispositivo` (en_linea=True, rssi, voltaje, temperatura, ram) **solo cada `ESTADO_INTERVALO_S`** por dispositivo.
4. Emitir por WebSocket `{"tipo":"telemetria", ...}` (sección 10).
5. Responder en < 50 ms (nada de I/O lento aquí).

### 7.3 `POST /api/v1/eventos/` (inmediato)
Body real:
```json
{"codigo":"NODO-01","evento_uid":"ABCD1234-1","tipo":"VIBRACION",
 "ts_ms":1760330000113,"ts_fuente":"SERVIDOR",
 "valor":0.82,"umbral":0.5,"unidad":"g",
 "aceleracion_g":1.31,"nivel_audio_db":62.4,
 "gps":{"fix":true,"sats":7,"lat":-16.398912,"lon":-71.536987,"vel_kmh":0,"alt_m":2335,"hdop":1.1},
 "imu":{"pitch_deg":0.4,"roll_deg":-1.2},
 "wifi":{"rssi_dbm":-58},"bateria":{"v":3.9,"pct":65}}
```
`tipo` ∈ {`VIBRACION`,`RUIDO`,`MANIPULACION`,`MOVIMIENTO`} (código de `tipo_evento`; si no existe → 400).

Respuesta **202** inmediata (no esperar a YOLO ni a Telegram):
```json
{"ok":true,"encolado":true,"evento_uid":"ABCD1234-1","server_ts_ms":1760330000140}
```
Si el `evento_uid` ya fue recibido en los últimos 10 min (LRU en memoria) → **200** `{"ok":true,"duplicado":true,...}` sin volver a procesar (el firmware reintenta hasta 3 veces).

### 7.4 API de la ESP32-CAM (la consume `ClienteCamaraESP32`)
Base: `config.url_camara(codigo)` (ej. `http://10.144.55.120`). Cabecera `X-API-Key: config.esp32cam.api_key`.
- `GET /capture` → `image/jpeg`. Cabeceras: `X-Device-Code`, `X-Frame-Width`, `X-Frame-Height`, `X-Capture-Ms`, `X-Uptime-Ms`. `?flash=1` enciende el LED.
- `GET /status` → `{"ok":true,"codigo":"CAM-NODO-01","firmware":"2.0.0","mac":"..","ip":"..","rssi_dbm":-61,"uptime_ms":..,"ram_libre_kb":..,"psram_libre_kb":..,"resolucion":"VGA","calidad_jpeg":12,"capturas":120,"fallos":0}`
- `GET /config?resolucion=VGA&calidad=12` → igual que `/status` (400 si parámetros inválidos).
- La cámara atiende **un cliente a la vez** → `ClienteCamaraESP32` usa un `threading.Lock` por cámara para que el pipeline de eventos y la vigilancia visual no choquen.

---

## 8. Lógica de negocio (clases y flujos)

### 8.1 `IncidenciaService.procesar_evento_sensor(evento)` — pipeline (lo ejecuta `ProcesadorEventos` en su hilo)
Registra cada marca de tiempo con `RelojServidor.ahora()` (datetime UTC con ms):
1. `t_deteccion_sensor` = `ts_ms` del ESP32 convertido a datetime UTC (si `ts_ms` es null → `t_recepcion` y `_sin_reloj_sensor=True`). `t_recepcion_servidor` = `request.t_recepcion_ms`.
2. Captura: `ClienteCamaraESP32.capturar()` (timeout `ESP32CAM_TIMEOUT_S`). Si falla → la incidencia se registra **sin imagen** y la alerta se envía como texto (`sendMessage` + `sendLocation`); se anota el error en `observacion`.
3. `t_inicio_inferencia` → `DetectorYOLO.detectar(frame)` → `t_fin_inferencia`. Devuelve un objeto `ResultadoDeteccion` (lista de `CajaDeteccion(clase, confianza, x1,y1,x2,y2)`, `imagen_anotada_jpg`, `hay_persona`, `confianza_max`).
4. **Tipo final:** si YOLO detecta `person` con confianza ≥ `YOLO_CONFIANZA_ALERTA` → tipo `PERSONA` y en `observacion` se escribe `Disparado por sensor: VIBRACION (0.82 g ≥ 0.5 g)`; si no, se conserva el tipo del sensor.
5. Guardar en **una transacción**: `Incidencia` (fecha_hora = t_deteccion_sensor, aceleracion_g, nivel_audio_db, lat/lon si `gps.fix`, imagen original y anotada con nombre `evt_<AAAAMMDD_HHMMSS_mmm>_<codigo>.jpg`) + todas las `DeteccionYolo`. → `t_registro_bd`.
6. Alerta: si `TELEGRAM_ALERTAR_SIN_PERSONA` es True o hubo persona → `TelegramService.enviar_alerta(incidencia, imagen)` → `t_envio_telegram` / `t_confirmacion_telegram`; actualiza `incidencia.estado = "NOTIFICADA"` si se envió.
7. Guardar `MedicionTiempo` con todas las marcas.
8. Emitir por WebSocket `incidencia_nueva` y, al cerrar la alerta, `incidencia_actualizada`.
9. (Opcional, después de todo) geocodificación inversa con Google si `GOOGLE_GEOCODING=True` → agrega “Dirección aprox.: …” a `observacion`. Nunca antes de la alerta (no debe sumar latencia).
10. `close_old_connections()` al terminar cada trabajo del hilo.

### 8.2 `VigilanciaVisual` (worker, YOLO continuo)
- Solo si `VISION_CONTINUA=True` y el dispositivo está `ACTIVO` (armado).
- Cada `VISION_INTERVALO_MS`: `t_solicitud` → `capturar()` → `t_frame` → YOLO → publica en `EstadoEnVivo` el último JPEG anotado (+ fps, n.º de personas, latencia de captura) y guarda `media/live/<codigo>.jpg`; emite `frame` por WebSocket.
- Si hay persona con confianza ≥ `YOLO_CONFIANZA_ALERTA` en `VISION_FRAMES_CONFIRMACION` frames seguidos y pasó `VISION_COOLDOWN_S` desde la última incidencia PERSONA de ese dispositivo → crea incidencia `PERSONA` reutilizando el mismo pipeline (pasos 3–10) con `t_deteccion_sensor = t_solicitud` del primer frame confirmado y `t_recepcion_servidor = t_frame`.
- Calcula FPS real con una media móvil de los últimos 20 frames.

### 8.3 `MonitorDispositivos` (worker, cada 5 s)
- Si `ahora - ultimo_contacto > DISPOSITIVO_TIMEOUT_S` → dispositivo **fuera de línea**: emite `dispositivo_estado` y, mientras siga caído, inserta `estado_dispositivo(en_linea=False)` cada `ESTADO_INTERVALO_S` (así la disponibilidad se puede calcular por muestreo).
- Cada `ESP32CAM_STATUS_S`: `GET /status` de la cámara → actualiza `dispositivo.mac_esp32cam` y el estado de la cámara en `EstadoEnVivo`.

### 8.4 `TelegramService`
- `enviar_alerta(incidencia, imagen_jpg|None) -> AlertaTelegram`: crea el registro `PENDIENTE`, envía `sendPhoto` (multipart) con `caption` en `parse_mode=HTML` (máx. 1024 caracteres) y `reply_markup` con botón URL **“📍 Ver ubicación”** (Google Maps) y, si `URL_PUBLICA` está configurada, **“🖥️ Ver incidencia”**. Reintentos con backoff (0.5 s, 1 s, 2 s). Guarda `message_id`, `codigo_http`, `intentos`, `error`, `estado_envio`, `fecha_envio`.
- Texto del mensaje:
  ```
  🚨 <b>ALERTA: Persona detectada (YOLO)</b> — riesgo ALTO
  🚗 <b>V1A-234</b> · Toyota Yaris · Plata
  👤 Juan Pérez · DNI 71234567
  🕒 13/10/2026 23:41:05
  📈 Confianza YOLO: 87 % · 1 persona
  🔎 Disparado por: VIBRACION 0.82 g
  ```
- Si el conductor no tiene `telegram_chat_id` → alerta `FALLIDO` con error claro, visible en la web.
- `enviar_prueba(chat_id)` para el botón de la página Configuración.
- `obtener_chats_recientes()` (getUpdates) para el comando `telegram_chat_ids` y para un botón “Buscar chat_id” en el formulario del conductor.
- Si `TELEGRAM_HABILITADO=False` → simula el envío (estado `ENVIADO`, `codigo_http=0`, marca en `error`: “simulado”).

### 8.5 `MapasService`
- `url_google_maps(lat, lon)`, `url_static_map(lat, lon)` (Static Maps API, solo si hay clave; se muestra en el detalle de la incidencia), `direccion_aproximada(lat, lon)` (Geocoding API, opcional).
- El frontend usa Google Maps JS si hay clave; si no, Leaflet + OpenStreetMap.

### 8.6 `MetricasService` (resultados de la tesis) — con rango de fechas y filtro por dispositivo/tipo
- Para cada tiempo (`latencia_red_ms`, `tiempo_inferencia_ms`, `tiempo_bd_ms`, `latencia_telegram_ms`, `tiempo_respuesta_total_ms`, `fps`): **n, media, desviación estándar, mínimo, máximo, mediana (p50), p95**. Además el **tiempo de captura derivado** = `t_inicio_inferencia − t_recepcion_servidor`.
- **Matriz de confusión** a partir de `incidencia.validacion` (VP, FP, FN, VN) y métricas: Precisión = VP/(VP+FP), Exhaustividad (Recall) = VP/(VP+FN), F1 = 2PR/(P+R), Exactitud = (VP+VN)/total. Indicar cuántas incidencias faltan validar.
- **Disponibilidad (%)** = filas `estado_dispositivo` con `en_linea=True` / total de filas del periodo × 100.
- **Alertas:** total, enviadas, fallidas, tasa de éxito, intentos promedio.
- Incidencias por tipo, por nivel de riesgo, por día y por hora del día.
- `ExportacionService`: Excel (`openpyxl`) con hojas **Resumen**, **Tiempos** (una fila por incidencia), **Matriz de confusión**, **Disponibilidad**, **Alertas**, **Incidencias**; y CSV por tabla. Títulos de las hojas listos para pegar en la tesis (“Tabla 25. Tiempo de respuesta entre el evento y la alerta”, “Tabla 26. Disponibilidad del sistema y alertas generadas”).

---

## 9. FRONTEND — sistema de monitoreo web (responsive, formal)

### 9.1 Identidad visual (OBLIGATORIA)
- **Solo dos colores: verde `#0cb967` y blanco `#ffffff`.** No uses rojo, azul, amarillo ni grises de otro tono. Para que el texto sea legible se permiten **únicamente variantes tonales del mismo verde** (más oscuras o con transparencia), definidas como variables CSS:
  ```css
  :root{
    --verde:#0cb967;            /* color de marca: sidebar, botones, bordes, íconos, gráficos */
    --verde-oscuro:#07854a;     /* texto verde sobre blanco y hover (contraste ≥ 4.5:1) */
    --verde-texto:#0a3d24;      /* texto principal sobre blanco */
    --verde-10:rgba(12,185,103,.10);  --verde-20:rgba(12,185,103,.20);  --verde-40:rgba(12,185,103,.40);
    --blanco:#ffffff;
  }
  ```
  (`#0cb967` sobre blanco tiene contraste 2.6:1: úsalo para rellenos, bordes, íconos y texto grande en negrita; para texto normal usa `--verde-texto` o `--verde-oscuro`.)
- **Estados sin otros colores:** riesgo ALTO = insignia verde sólida + ícono `bi-exclamation-octagon-fill` + negrita; MEDIO = insignia con borde verde; BAJO = insignia `--verde-10`. Dispositivo fuera de línea = ícono `bi-wifi-off`, texto tachado/atenuado (opacidad) y borde punteado. Diferencia series de gráficos con opacidad y trazo discontinuo, nunca con otro color.
- **Tipografía:** “Montserrat” (títulos, 600–700) y “Open Sans” (texto), descargadas a `static/vendor/fonts/` (respaldo `system-ui, "Segoe UI", sans-serif`). Escala sobria, números con `font-variant-numeric: tabular-nums`.

### 9.2 Layout (`base.html`)
- **Fondo:** el logo `logo_ucsm_fondo.png` **grande, centrado y fijo en toda la página** como marca de agua:
  ```css
  body{background:#fff;}
  body::before{content:"";position:fixed;inset:0;z-index:0;pointer-events:none;
    background:url("../img/logo_ucsm_fondo.png") center/min(75vw,900px) no-repeat;opacity:.07;}
  .app{position:relative;z-index:1;}
  .tarjeta{background:rgba(255,255,255,.92);backdrop-filter:blur(2px);border:1px solid var(--verde-20);
           border-radius:14px;box-shadow:0 2px 10px var(--verde-10);}
  ```
- **Encabezado (topbar, blanco, 64 px, borde inferior 3 px `--verde`):** a la izquierda **`logo_ucsm_letra.png`** (alto 40–44 px) seguido del título “Sistema de Monitoreo Vehicular Inteligente” y subtítulo “YOLO + IoT · UCSM”; a la derecha: indicador de conexión en tiempo real (punto verde que pulsa = conectado; anillo vacío = reconectando), reloj en vivo, interruptor **Armar/Desarmar vigilancia** del dispositivo seleccionado y menú del usuario (perfil, cerrar sesión).
- **Menú lateral izquierdo (sidebar, 260 px, fondo `--verde`, texto blanco 15 px peso 600, íconos Bootstrap Icons):** ítem activo = píldora blanca con texto `--verde-oscuro`. Secciones:
  - **Monitoreo:** Panel en tiempo real · Incidencias · Mapa de incidencias · Alertas Telegram
  - **Registro:** Conductores · Vehículos · Dispositivos · Tipos de evento
  - **Análisis:** Resultados y métricas
  - **Sistema:** Configuración · Administración (Django admin)
  - Contador en vivo de incidencias sin validar junto a “Incidencias”.
- **Responsive:** ≥ 992 px sidebar fijo; < 992 px sidebar como *offcanvas* con botón hamburguesa en el topbar; KPIs 4 → 2 → 1 columnas; tablas con scroll horizontal o convertidas en tarjetas en móvil; imágenes `max-width:100%`. Probar en 360, 768, 1280 y 1920 px.
- Pie de página discreto: “Universidad Católica de Santa María — Tesis 2026”.
- Accesibilidad: foco visible (`outline` verde 2 px), `aria-label` en botones de ícono, textos alternativos en imágenes.

### 9.3 Páginas
1. **Login** (`/login/`): tarjeta centrada con `logo_ucsm_letra.png`, fondo con marca de agua; Django auth. Todo el sistema requiere sesión (`LoginRequiredMixin`).
2. **Panel en tiempo real** (`/`):
   - Fila KPI: *Incidencias hoy* · *Alertas enviadas hoy / tasa de éxito* · *Tiempo de respuesta promedio (hoy, ms)* · *Disponibilidad 24 h (%)*.
   - **Estado del nodo** (ESP32-S3): en línea/fuera de línea, último contacto (“hace 2 s”), RSSI, batería (barra), temperatura, armado/desarmado, fuente de reloj y RTT.
   - **Cámara en vivo:** último frame anotado por YOLO (se refresca al llegar el mensaje `frame`), FPS, personas detectadas, latencia de captura; estado de la ESP32-CAM.
   - **Telemetría en vivo** (Chart.js, últimos 60 s, sin animaciones pesadas): vibración pico-pico (g), nivel sonoro (dB SPL) y |a| máx.; líneas de umbral punteadas.
   - **Mapa:** posición actual del vehículo (último GPS) + últimas incidencias como marcadores.
   - **Feed de incidencias:** últimas 10; las nuevas entran arriba con animación sutil, **toast** + sonido corto opcional + `Notification API` del navegador (pide permiso una vez).
3. **Incidencias** (`/incidencias/`): tabla paginada (20) con miniatura, fecha, tipo, riesgo, placa, estado, validación, tiempo total; filtros (rango de fechas, tipo, riesgo, estado, validación, placa) y exportar CSV/Excel.
   **Detalle** (`/incidencias/<id>/`): imagen original y anotada (pestañas, clic para ampliar), tabla de detecciones YOLO, **línea de tiempo** de las etapas con sus ms (barra horizontal apilada), datos del sensor, mapa (Static Maps o Leaflet) + botón “Abrir en Google Maps”, alertas enviadas (con botón **Reintentar**), formulario para **validar (VP/FP/FN/VN)**, cambiar estado y escribir observación.
4. **Mapa de incidencias** (`/incidencias/mapa/`): todas las incidencias con coordenadas, filtros por fecha/tipo, agrupación de marcadores.
5. **Conductores, Vehículos, Dispositivos, Tipos de evento:** CRUD completo (lista con búsqueda, crear, editar, detalle, eliminar con confirmación; respeta `PROTECT` mostrando un mensaje amable si tiene registros asociados). El formulario de conductor tiene el botón “Buscar chat_id en Telegram”. El detalle del dispositivo muestra el historial de `estado_dispositivo` (gráfica RSSI/voltaje), disponibilidad, botones **Armar/Desarmar**, **Probar cámara** (muestra una captura) y la URL de la cámara configurada.
6. **Alertas Telegram** (`/alertas/`): historial con estado, intentos, código HTTP y error; filtro por estado; reintentar las fallidas.
7. **Resultados y métricas** (`/resultados/`): selector de rango de fechas; tabla estadística de tiempos (n, media, DE, mín, máx, p50, p95); histograma del tiempo de respuesta total; serie temporal de latencias; desglose promedio por etapa (barra apilada: captura, inferencia, BD, Telegram); FPS; matriz de confusión 2×2 + precisión, recall, F1 y exactitud; disponibilidad; alertas; incidencias por tipo/hora. Botón **Exportar Excel para la tesis**.
8. **Configuración** (`/configuracion/`): muestra `config.resumen_seguro()` agrupado (secretos enmascarados), y botones de prueba: *Enviar mensaje de prueba a Telegram*, *Probar cámara*, *Probar YOLO* (subir una imagen y ver el resultado anotado y el tiempo de inferencia), *Ver umbrales enviados al ESP32*.

### 9.4 JavaScript
- Módulos ES sin frameworks pesados: `ws.js` (clase `ConexionTiempoReal` con reconexión exponencial y *fallback* a sondeo de `/api/v1/panel/estado/` cada 2 s si el WebSocket no conecta), `panel.js` (clase `PanelTiempoReal`), `mapa.js` (clase `MapaVehicular` que abstrae Google Maps / Leaflet), `resultados.js`.
- Fechas en hora de Lima (`America/Lima`) con formato `dd/mm/aaaa HH:MM:SS`.

---

## 10. Tiempo real (Channels)

- `asgi.py`: `ProtocolTypeRouter({"http": django_asgi_app, "websocket": AuthMiddlewareStack(URLRouter(routing.websocket_urlpatterns))})`.
- `ws/panel/` → `PanelConsumer` (rechaza usuarios no autenticados) que se une al grupo `panel`.
- `NotificadorTiempoReal` (usado desde servicios y workers con `async_to_sync(channel_layer.group_send)`), mensajes:
  ```json
  {"tipo":"telemetria","codigo":"NODO-01","data":{...lectura resumida...}}
  {"tipo":"incidencia_nueva","data":{"id":15,"tipo":"PERSONA","nombre_tipo":"Persona detectada (YOLO)","riesgo":"ALTO","placa":"V1A-234","fecha_hora":"2026-10-13T04:41:05.113Z","miniatura":"/media/anotadas/2026/10/evt_....jpg","lat":-16.3989,"lon":-71.5369,"personas":1,"confianza_max":0.87}}
  {"tipo":"incidencia_actualizada","data":{"id":15,"estado":"NOTIFICADA","alerta":"ENVIADO","tiempo_total_ms":812.4}}
  {"tipo":"dispositivo_estado","data":{"codigo":"NODO-01","en_linea":false,"ultimo_contacto":"..."}}
  {"tipo":"frame","data":{"codigo":"NODO-01","url":"/media/live/NODO-01.jpg?ts=1760330000123","fps":3.8,"personas":0,"latencia_captura_ms":142}}
  ```
- API JSON para el frontend (sesión): `GET /api/v1/panel/estado/`, `GET /api/v1/panel/incidencias/recientes/`, `GET /api/v1/panel/metricas/?desde&hasta&dispositivo`, `POST /api/v1/panel/dispositivos/<id>/armar/` (`{"armado":true|false}` → cambia `estado` ACTIVO/INACTIVO; el ESP32 lo recibe en su siguiente respuesta de telemetría).

---

## 11. Herramientas para probar SIN hardware

- `python manage.py simular_nodo --codigo NODO-01 --url http://127.0.0.1:8000 --segundos 120 --eventos 5`: clase `SimuladorNodo` que envía telemetría **exactamente con el JSON de la sección 7.2** cada 1 s (valores realistas con ruido, GPS en Arequipa), aplica el `server_ts_ms` para sincronizar su reloj igual que el firmware, y dispara eventos aleatorios (sección 7.3) con `evento_uid` único.
- `ESP32CAM_SIMULADA=True` → `CamaraSimulada` devuelve imágenes de `media/simulacion/*.jpg` (genera 3 imágenes de ejemplo con Pillow si la carpeta está vacía) con un retardo artificial de 80–150 ms.
- `python main.py --simular` combina ambas cosas con Telegram deshabilitado.
- `sembrar_catalogos --demo` crea además un conductor, un vehículo y el dispositivo `NODO-01` de ejemplo, y el superusuario `admin` / `admin123` (avisar en consola que lo cambien).

---

## 12. Seguridad y robustez
- Login obligatorio en toda la web; `/media/` servido por `media_controller` solo a usuarios autenticados (`FileResponse`), estáticos con WhiteNoise.
- CSRF activo en formularios web; exento solo en `/api/v1/telemetria/` y `/api/v1/eventos/`.
- Validación estricta con serializers DRF (rangos: lat −90..90, lon −180..180, rssi −120..0, etc.).
- Límite de tamaño de body (64 KB) para la API del ESP32.
- Manejo de errores: ningún hilo debe morir por una excepción (captura, registra en log y continúa). Si YOLO no carga, el sistema sigue funcionando (incidencias sin detecciones) y la web lo muestra.
- Logging estructurado por módulo; en el log de cada incidencia una línea con todos los tiempos.

---

## 13. Pruebas (`monitoreo/tests/`)
- API: 401 sin key, 404 dispositivo inexistente, 400 JSON inválido, 200 telemetría (actualiza `dispositivo` y respeta el intervalo de `estado_dispositivo`), 202 evento, 200 duplicado.
- `MedicionTiempo.save()`: cálculos correctos (incluido `_sin_reloj_sensor`).
- Pipeline completo con dobles de prueba (cámara simulada, YOLO falso, Telegram falso) → crea `Incidencia` + `DeteccionYolo` + `MedicionTiempo` + `AlertaTelegram`, y cambia el tipo a `PERSONA` cuando corresponde.
- `MetricasService`: estadísticas, matriz de confusión y disponibilidad con datos sembrados.
- `PanelConsumer`: rechaza anónimos, recibe `incidencia_nueva`.
- Vistas web: login requerido, CRUD de conductor/vehículo/dispositivo.

---

## 14. Criterios de aceptación (verifícalos antes de terminar)
- [ ] `python main.py` levanta todo en `http://<IP-LAN>:8000` y muestra el banner con la IP para el firmware.
- [ ] `python main.py --simular` + `python manage.py simular_nodo` → el panel muestra telemetría en vivo, aparecen incidencias con imagen anotada, la línea de tiempo tiene todos los ms y la página Resultados calcula estadísticas.
- [ ] Con hardware real: el ESP32-S3 recibe `200` en telemetría y `202` en eventos (revisar Monitor Serie: `reloj=SERVIDOR`), y la ESP32-CAM responde en `/capture`.
- [ ] Telegram recibe la foto anotada con placa, marca, color, propietario, confianza y botón de ubicación.
- [ ] Armar/Desarmar desde la web cambia el comportamiento del ESP32 en ≤ 2 s.
- [ ] La web usa solo `#0cb967` y blanco (más variantes tonales del verde), con el logo de letras en el encabezado, el logo grande de fondo, menú a la izquierda, y es usable en un celular de 360 px.
- [ ] `python manage.py test` pasa completo; `python manage.py check --deploy` sin errores críticos (salvo los de DEBUG en desarrollo).
- [ ] La exportación Excel abre correctamente y contiene las hojas indicadas.
- [ ] `README.md` explica: instalación (`pip install -r requirements.txt`), `.env`, cargar el firmware (librerías TinyGPSPlus y ArduinoJson 7), registrar conductor/vehículo/dispositivo `NODO-01`, obtener el `chat_id`, ejecutar, y probar sin hardware.

---

## 15. Orden de implementación (fases)
1. **Base:** `config.py`, `.env(.example)`, `settings.py` (apps, DRF, Channels, WhiteNoise, media, zona horaria `America/Lima`, `USE_TZ=True`, logging), modelos (sección 6), migraciones, admin, `sembrar_catalogos`.
2. **API del firmware:** middleware de marca de tiempo, autenticación por API key, serializers, `TelemetriaService`, `EstadoEnVivo`, endpoints 7.2 y 7.3, `simular_nodo`, tests de API.
3. **Pipeline:** `ClienteCamaraESP32`/`CamaraSimulada`, `DetectorYOLO`, `IncidenciaService`, `TelegramService`, `MapasService`, `ProcesadorEventos`, tests del pipeline.
4. **Workers y servidor:** `VigilanciaVisual`, `MonitorDispositivos`, `main.py` (`ServidorPrincipal`), modo WAL, asgi/Channels, `PanelConsumer`, `NotificadorTiempoReal`.
5. **Frontend:** sistema de diseño (`app.css`), `base.html`, login, panel en tiempo real, incidencias (lista/detalle/mapa), CRUDs, alertas, configuración.
6. **Resultados:** `MetricasService`, `DisponibilidadService`, `ExportacionService`, página Resultados, comando `exportar_resultados`.
7. **Cierre:** pruebas completas, revisión responsive (360/768/1280/1920 px), `README.md`, limpieza de código y verificación de los criterios de la sección 14.
