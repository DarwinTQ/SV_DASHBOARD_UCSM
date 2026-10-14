# Sistema de Monitoreo Vehicular Inteligente (YOLO + IoT) · UCSM

Sistema IoT para vehículos particulares que detecta eventos de riesgo (golpes,
ruidos fuertes, manipulación, movimiento no autorizado y presencia de
personas detectada por YOLO), guarda cada incidencia con su imagen en SQLite,
envía la alerta al propietario por Telegram y mide los tiempos de cada etapa
(latencia, inferencia, BD, Telegram, respuesta total) para el Capítulo V de
la tesis.

Backend en Django (POO, arquitectura MVC + capa de servicios) con Channels
para el panel en tiempo real, YOLO (ultralytics) para la detección de
personas, y dos nodos de firmware ESP32 (sensores + cámara).

---

## 1. Instalación

Requisitos: Python 3.11+ (probado con 3.14), Windows (rutas con `pathlib`,
sin nada específico de Linux), y el proyecto corre **localmente** en la PC
del estudiante, en la misma red Wi-Fi que los ESP32.

```powershell
# Clonar / copiar el proyecto y entrar a la carpeta
cd SV_INTERFAZ_UCSM

# Crear y activar el entorno virtual (si no existe ya .venv)
python -m venv .venv
.venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

### Configurar `.env`

Copia la plantilla y ajusta lo necesario (el archivo ya viene con valores por
defecto razonables para pruebas locales):

```powershell
copy .env.example .env
```

Variables más importantes:

| Variable | Para qué sirve |
|---|---|
| `DJANGO_SECRET_KEY` | Clave de Django. Si la dejas vacía, el sistema genera una temporal y avisa en consola. |
| `ESP32_API_KEY` | Cabecera `X-API-Key` que debe enviar el ESP32-S3 (debe coincidir con `API_KEY` en el `.ino`). |
| `ESP32CAM_URLS` | `CODIGO=http://ip` de cada ESP32-CAM, ej. `NODO-01=http://192.168.1.120`. |
| `ESP32CAM_SIMULADA` | `True` para usar imágenes de `media/simulacion/` sin hardware. |
| `TELEGRAM_BOT_TOKEN` | Token del bot de Telegram (ver sección 4). Vacío = no se pueden enviar alertas reales. |
| `TELEGRAM_HABILITADO` | `False` simula el envío (no llama a la API real de Telegram). |
| `GOOGLE_MAPS_API_KEY` | Opcional; sin ella la web usa Leaflet/OpenStreetMap. |
| `URL_PUBLICA` | Ej. `http://192.168.1.10:8000`; se usa en el botón "Ver incidencia" de Telegram. |

---

## 2. Ejecución

El **único punto de arranque** del sistema es `main.py` (nunca
`manage.py runserver` para uso real: ese comando no arranca los workers de
fondo ni Daphne/Channels).

```powershell
.venv\Scripts\activate
python main.py
```

Esto hace, en orden: verifica carpetas (`media/`, `logs/`, `modelos_ia/`),
aplica migraciones, siembra el catálogo de tipos de evento, activa SQLite en
modo WAL, carga y calienta YOLO, arranca los workers (`ProcesadorEventos`,
`VigilanciaVisual`, `MonitorDispositivos`) y levanta Daphne. Al final imprime
un banner con la URL local, la IP de la LAN (para poner en el firmware), la
URL de la cámara configurada y el estado de Telegram/Google Maps.

Argumentos disponibles:

```powershell
python main.py --host 0.0.0.0 --puerto 8000   # por defecto
python main.py --sin-vision                   # desactiva YOLO continuo sobre la cámara
python main.py --simular                      # cámara simulada + Telegram deshabilitado (sección 3)
python main.py --solo-migrar                  # solo migra y siembra el catálogo, no levanta el servidor
```

Abre `http://127.0.0.1:8000/` e inicia sesión con el superusuario demo
(se crea automáticamente la primera vez que corres con `--simular`, o con el
comando de siembra de abajo):

```
usuario: admin
contraseña: admin123   ← cámbiala después de la primera entrada
```

---

## 3. Probar sin hardware

No necesitas el ESP32-S3 ni la ESP32-CAM para ver el sistema funcionando:

```powershell
# Terminal 1: levanta el servidor con cámara simulada y Telegram deshabilitado
python main.py --simular

# Terminal 2: emula el ESP32-S3 (telemetría cada 1 s + eventos aleatorios)
python manage.py simular_nodo --codigo NODO-01 --url http://127.0.0.1:8000 --segundos 120 --eventos 5
```

Con `--simular`, `ESP32CAM_SIMULADA` queda activo: la cámara responde con
imágenes de `media/simulacion/*.jpg` (se generan 3 de ejemplo automáticamente
si la carpeta está vacía) y las alertas de Telegram quedan en estado
`ENVIADO (simulado)` sin llamar a la API real.

Mientras corre la simulación: el panel (`/`) muestra telemetría en vivo,
aparecen incidencias con imagen anotada por YOLO, el detalle de cada
incidencia tiene la línea de tiempo completa en milisegundos, y la página
**Resultados** (`/resultados/`) calcula las estadísticas.

### Sembrar datos de ejemplo manualmente

Si prefieres no usar `--simular`, puedes sembrar el catálogo y los datos de
ejemplo (conductor, vehículo, dispositivo `NODO-01` y el superusuario
`admin`) en cualquier momento:

```powershell
python manage.py sembrar_catalogos --demo
```

---

## 4. Con hardware real (ESP32-S3 + ESP32-CAM)

### 4.1 Registrar el conductor, el vehículo y el dispositivo

Antes de encender los ESP32, registra en la web (o con `--demo` como arriba):

1. **Conductores** → nuevo conductor (DNI, nombres, teléfono).
2. **Vehículos** → nuevo vehículo, asociado a ese conductor (placa, marca, modelo…).
3. **Dispositivos** → nuevo dispositivo con **código `NODO-01`** (o el que uses
   en `CODIGO_DISPOSITIVO` del firmware) asociado a ese vehículo, estado `ACTIVO`.

El `codigo` del dispositivo **debe coincidir exactamente** con
`cfg::CODIGO_DISPOSITIVO` del `.ino` del ESP32-S3: si no existe en la tabla,
la API responde `404 dispositivo no registrado`.

### 4.2 Cargar el firmware

**`firmware/nodo_iot_esp32s3/nodo_iot_esp32s3.ino`** (sensores GPS + MPU-6050 + INMP441):

- Placa: **"ESP32S3 Dev Module"** (core arduino-esp32 3.x, Espressif).
- Librerías (Gestor de librerías del IDE): **TinyGPSPlus** (Mikal Hart) y
  **ArduinoJson 7.x** (Benoit Blanchon).
- Herramientas → **USB CDC On Boot: "Enabled"**.
- Edita en `namespace cfg { ... }`: `WIFI_SSID`, `WIFI_PASS`,
  `SERVIDOR_BASE` (la IP de la LAN que imprime el banner de `main.py`, ej.
  `http://192.168.1.10:8000`), `API_KEY` (= `ESP32_API_KEY` del `.env`) y
  `CODIGO_DISPOSITIVO` (= código del dispositivo registrado en el paso 4.1).

**`firmware/esp32cam_camara/esp32cam_camara.ino`** (cámara OV2640):

- Placa: **"AI Thinker ESP32-CAM"** (core arduino-esp32 3.x).
- Librería: **ArduinoJson 7.x**.
- Alimentación: 5 V / 2 A estable (los reinicios aleatorios suelen ser por
  fuente débil, no por el código).
- Edita `WIFI_SSID`, `WIFI_PASS` y `API_KEY` (= `ESP32CAM_API_KEY` del `.env`).
- Una vez conectada, anota su IP (Monitor Serie) y agrégala a
  `ESP32CAM_URLS` en `.env`, ej. `NODO-01=http://192.168.1.120`.

### 4.3 Vincular al propietario y a su familia por Telegram

La vinculación ya **no** se hace copiando un `chat_id` a mano: todo pasa por
el bot (sección 8 de abajo). En resumen: crea el bot con **@BotFather**,
copia el token a `TELEGRAM_BOT_TOKEN` en `.env`, y el propietario se vincula
escribiéndole `/vincular DNI TELEFONO` al bot desde su celular.

### 4.4 Ejecutar con hardware

```powershell
python main.py
```

Revisa el Monitor Serie del ESP32-S3: debería mostrar `reloj=SERVIDOR` tras
el primer `POST /api/v1/telemetria/` exitoso (200), y `202` al reportar un
evento. La ESP32-CAM debe responder en `GET /capture` cuando el servidor se
lo pida (ocurre automáticamente al procesar un evento o con la vigilancia
visual continua activa).

---

## 5. Comandos útiles

```powershell
python manage.py check                 # verificación rápida de Django
python manage.py test                   # toda la suite de pruebas
python manage.py migrate                 # aplicar migraciones (main.py ya lo hace solo)
python manage.py sembrar_catalogos --demo
python manage.py simular_nodo --codigo NODO-01 --url http://127.0.0.1:8000 --segundos 60 --eventos 3
python manage.py exportar_resultados --desde 2026-01-01 --hasta 2026-12-31
python manage.py createsuperuser         # si necesitas otro usuario además de "admin"
```

---

## 6. Bot de Telegram multiusuario (propietario + familiares)

Un solo bot atiende a **todas** las personas autorizadas de cada vehículo
(long polling con `getUpdates`; el servidor es local, sin IP pública, así
que **no** se usa webhook). Lo arranca `main.py` automáticamente si hay
`TELEGRAM_BOT_TOKEN` y `TELEGRAM_BOT_COMANDOS=True` (por defecto).

### 6.1 Vincular al propietario

El propietario le escribe al bot:

```
/vincular 71234567 987654321
```

(su DNI y su teléfono, tal cual están registrados en **Conductores**). El
bot borra ese mensaje de inmediato (para que el DNI no quede visible en el
chat) y, si coincide, vincula ese chat a **todos** los vehículos de ese
conductor como propietario (con permiso de armar/desarmar). Hay un límite de
5 intentos fallidos por hora por chat.

### 6.2 Invitar a la familia

El propietario invita desde el bot (`/invitar <nombre> [parentesco]`) o
desde la web (**Vehículos → detalle → "Invitar familiar"**, que además
genera un código QR). Cualquiera de las dos vías entrega un código de 6
caracteres y un enlace `https://t.me/<bot>?start=CODIGO` para reenviar por
WhatsApp; el familiar lo abre o escribe `/unirse CODIGO` y queda vinculado
(por defecto solo puede **consultar**; el propietario le da permiso de
controlar con `/familia` o desde la web). Los códigos vencen a las
`TELEGRAM_INVITACION_HORAS` horas (24 por defecto) y cada vehículo admite
hasta `TELEGRAM_MAX_ACCESOS_POR_VEHICULO` accesos (6 por defecto).

### 6.3 Qué puede hacer cada quien

| | Propietario | Familiar | Familiar con permiso | Admin (`TELEGRAM_ADMIN_CHAT_IDS`) |
|---|---|---|---|---|
| Consultar estado/foto/ubicación/sensores | ✔ | ✔ | ✔ | ✔ |
| Recibir y validar alertas | ✔ | ✔ | ✔ | ✔ |
| Armar/desarmar | ✔ | ✖ | ✔ | ✔ |
| Invitar/revocar/dar permisos | ✔ | ✖ | ✖ | ✔ |

Comandos principales: `/estado`, `/foto`, `/ubicacion`, `/sensores`,
`/bateria`, `/ultima`, `/incidencias`, `/armar`, `/desarmar`, `/alertas
on|off`, `/silenciar <min>`, `/familia`, `/revocar`, `/vehiculo`,
`/metricas` — todos aceptan la placa como argumento opcional si el chat
tiene acceso a más de un vehículo (`/foto V1A-234`); sin ella, si hay varios
el bot muestra botones para elegir. `/ayuda` lista los comandos según el rol.

### 6.4 Alertas y validación compartida

Cuando ocurre un evento (YOLO, golpe, ruido, manipulación, movimiento), la
alerta con la foto anotada llega **a la vez** a todos los que tengan
`/alertas on` (y no estén silenciados con `/silenciar`), con botones
**"✅ Es real" / "❌ Falsa alarma"**. El primero que valida gana: la
incidencia queda marcada y los botones se actualizan en los celulares de
**todos** los que recibieron esa alerta, mostrando quién la validó y a qué
hora; si alguien más intenta validar después, el bot le avisa que ya fue
atendida.

### 6.5 Probar sin un bot real

Si `TELEGRAM_BOT_TOKEN` está vacío (o `python main.py --simular`, que además
pone `TELEGRAM_HABILITADO=False`), el worker del bot simplemente no arranca
o las alertas quedan como `ENVIADO (simulado)` — el resto del sistema sigue
funcionando igual. El banner de `main.py` siempre indica el estado real:
`Bot @<usuario> activo (long polling)` o el motivo por el que no arrancó.

⚠️ El token de un bot solo puede estar siendo consultado (`getUpdates`) por
**un** programa a la vez: si lo abres también en otro script o con un
webhook, Telegram responde `409 Conflict`.

---

## 7. Estructura del proyecto

```
main.py                 # ServidorPrincipal — único punto de arranque real
config.py               # Config — toda la configuración, lee .env
sv_ucsm/                # paquete Django (settings, urls, asgi, wsgi)
monitoreo/              # app principal (MVC + servicios + workers)
  models/               # M — modelos (sección 6, fijo) + AccesoTelegram
  controllers/web/      # C — class-based views de la web
  controllers/api/      # C — endpoints REST (firmware + panel)
  templates/monitoreo/  # V — plantillas
  services/             # lógica de negocio (YOLO, Telegram, mapas, métricas…)
  workers/              # hilos de fondo (los arranca main.py), incl. el bot
  bot/                  # ClienteBotAPI, comandos, teclados, invitaciones…
  repositories/         # consultas ORM reutilizables
  consumers/, routing.py  # WebSocket (Channels)
static/vendor/          # Bootstrap, Bootstrap Icons, Chart.js, Leaflet, fuentes (descargados, sin CDN)
media/                  # capturas, imágenes anotadas, frames en vivo
modelos_ia/             # pesos de YOLO (yolo26n.pt, se descarga solo si falta)
firmware/               # .ino del ESP32-S3 y de la ESP32-CAM
logs/sistema.log, logs/bot_comandos.csv  # logs (rotativo 5 MB×5, y CSV de uso del bot)
```

---

## 8. Notas de seguridad

- Toda la web requiere inicio de sesión; `/media/` solo es accesible para
  usuarios autenticados (se sirve mediante una vista propia, no directamente
  por el servidor web).
- Los endpoints del firmware (`/api/v1/telemetria/`, `/api/v1/eventos/`) usan
  autenticación por `X-API-Key` (no por sesión) y están exentos de CSRF por
  ese motivo; el resto de la web sí exige CSRF.
- Ningún secreto (tokens, API keys) va escrito en el código: todo se lee de
  `.env` a través de `config.py`. La página **Configuración** muestra esos
  valores enmascarados.
