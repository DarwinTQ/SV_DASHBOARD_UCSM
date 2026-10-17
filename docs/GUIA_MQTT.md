# Guía MQTT — Sistema de Monitoreo Vehicular Inteligente (UCSM)

Guía de puesta en marcha de la versión **v3.0.0**, en la que el nodo ESP32-S3 y
la ESP32-CAM dejan de hablar HTTP y pasan a hablar **MQTT** contra un broker
Mosquitto que corre en la misma PC del servidor Django.

> Los endpoints `/api/v1/telemetria/` y `/api/v1/eventos/` **siguen existiendo**
> como respaldo. Si no puedes instalar Mosquitto, arranca el servidor con
> `python main.py --sin-mqtt` y usa `python manage.py simular_nodo --http`.

---

## 1. Por qué MQTT (justificación para la tesis)

| Flujo | Protocolo | Motivo |
|---|---|---|
| Nodo ESP32-S3 → servidor (telemetría, GPS, eventos, estado) | **MQTT** | Conexión persistente, cabecera mínima (2 bytes), QoS por tipo de mensaje, *Last Will* para detectar la desconexión al instante y comandos al nodo por suscripción |
| Servidor → nodo (armar/desarmar, umbrales, reloj) | **MQTT** (retenido) | El nodo recibe la configuración vigente en cuanto se conecta, sin que el servidor tenga que recordar reenviársela |
| ESP32-CAM → servidor (vídeo) | **HTTP MJPEG** `/stream` (puerto 81) | Estándar y eficiente en ESP32-CAM; una sola conexión persistente. MQTT no sirve para vídeo: el payload es grande y añadiría un salto por el broker |
| ESP32-CAM foto puntual | **HTTP** `/capture` (puerto 80) | Respaldo cuando el stream no está disponible, y única forma de usar el flash |
| ESP32-CAM estado y comandos | **MQTT** (`cam/estado` retenido + *Last Will*, `cam/cmd`) | Mismo canal de control que el nodo |
| Servidor → navegador | **WebSocket** (Django Channels) | Empuje en tiempo real al panel y al mapa |

**Regla de oro:** solo el servidor se conecta a la cámara. El navegador y
Telegram reciben las imágenes ya anotadas desde el servidor.

### Ganancia medible

| Antes (v2.0.0, HTTP) | Ahora (v3.0.0, MQTT) |
|---|---|
| Un `POST` nuevo (TCP + cabeceras HTTP) por cada telemetría | Una conexión abierta; 2 bytes de cabecera por mensaje |
| Caída detectada por *timeout* (10 s) | Caída detectada por *Last Will* en ≤ keepalive × 1.5 (≈ 22 s como máximo, normalmente al instante) |
| GPS a 1 Hz | GPS a **5 Hz** en marcha |
| Foto pedida a `/capture` tras el evento (cientos de ms) | Cuadro ya en memoria, el **más cercano al instante del sensor** |

---

## 2. Instalar Mosquitto en Windows

1. Descarga el instalador de 64 bits desde <https://mosquitto.org/download/>
   (sección *Windows*, `mosquitto-x.y.z-install-windows-x64.exe`).
2. Instálalo en la ruta por defecto `C:\Program Files\mosquitto\`.
   Marca la casilla de **Service** solo si quieres que arranque con Windows; el
   script del proyecto detiene ese servicio para usar la configuración de la
   tesis, que es la que tiene los usuarios y la ACL.
3. Comprueba la instalación:

   ```bat
   "C:\Program Files\mosquitto\mosquitto.exe" -h
   ```

### Archivos del proyecto

| Archivo | Para qué sirve |
|---|---|
| `infra\mosquitto\mosquitto.conf` | Configuración del broker (puerto, usuarios, ACL, persistencia, log) |
| `infra\mosquitto\acl` | Qué tópicos puede leer/escribir cada usuario |
| `infra\mosquitto\passwd` | Contraseñas cifradas (**no se versiona**; lo crea el `.bat`) |
| `infra\mosquitto\iniciar_mosquitto.bat` | Crea los usuarios la primera vez, abre el firewall y arranca el broker |

### Arrancar el broker

```bat
infra\mosquitto\iniciar_mosquitto.bat
```

La **primera vez** ejecútalo **como Administrador**: necesita permisos para
crear la regla de firewall del puerto 1883.

El script hace tres cosas por ti:

1. crea `infra\mosquitto\passwd` con los tres usuarios del sistema;
2. abre el puerto 1883/TCP en el perfil de red *privada*;
3. detiene el servicio de Windows (si está corriendo) y arranca Mosquitto en
   primer plano con la configuración del proyecto.

---

## 3. Usuarios y ACL

El broker **no acepta conexiones anónimas** (`allow_anonymous false`).

| Usuario | Clave de desarrollo | Quién lo usa | Permisos |
|---|---|---|---|
| `servidor` | `ucsm2026servidor` | Django (`PuenteMQTT`) | lectura y escritura de `ucsm/v1/#` |
| `nodo01` | `ucsm2026nodo` | ESP32-S3 | solo sus tópicos de `ucsm/v1/NODO-01/` |
| `cam01` | `ucsm2026cam` | ESP32-CAM | solo `cam/estado` (escribir) y `cam/cmd` (leer) |

> **Cambia estas claves antes de la defensa.** Son claves de desarrollo que
> están escritas en el `.bat` a propósito, para que el sistema arranque sin
> configuración manual.

### Cambiar una clave

```bat
cd "C:\Program Files\mosquitto"
mosquitto_passwd.exe -b D:\Tesis_2026\SV_INTERFAZ_UCSM\infra\mosquitto\passwd nodo01 LA_NUEVA_CLAVE
```

Después hay que reflejar el cambio en **tres** sitios:

| Usuario | Dónde se configura |
|---|---|
| `servidor` | `.env` → `MQTT_CLAVE` |
| `nodo01` | `firmware\nodo_iot_esp32s3\nodo_iot_esp32s3.ino` → `cfg::MQTT_CLAVE` |
| `cam01` | `firmware\esp32cam_camara\esp32cam_camara.ino` → `cfg::MQTT_CLAVE` |

Y reiniciar el broker para que recargue el archivo de contraseñas.

### Agregar un segundo vehículo

```bat
mosquitto_passwd.exe -b ...\passwd nodo02 clave_nodo02
mosquitto_passwd.exe -b ...\passwd cam02  clave_cam02
```

y duplicar en `infra\mosquitto\acl` los bloques de `nodo01` y `cam01`
cambiando `NODO-01` por `NODO-02`.

---

## 4. Firewall

| Puerto | Protocolo | Para qué | Quién lo abre |
|---|---|---|---|
| **1883** | TCP | Broker MQTT (nodo y cámara se conectan aquí) | `iniciar_mosquitto.bat` |
| **8000** | TCP | Panel web + WebSocket + API de respaldo | manual (ver abajo) |

Para abrir el 8000 a mano, en una consola **como Administrador**:

```bat
netsh advfirewall firewall add rule name="Django 8000 UCSM" dir=in action=allow protocol=TCP localport=8000 profile=private
```

La cámara no necesita reglas de entrada en la PC: es el servidor el que se
conecta a ella (puertos 80 y 81 del ESP32-CAM).

---

## 5. Comprobar que el broker funciona

Con el broker corriendo, en **otra** consola:

```bat
cd "C:\Program Files\mosquitto"
mosquitto_sub -h 127.0.0.1 -u servidor -P ucsm2026servidor -t "ucsm/v1/#" -v
```

Deberías ver aparecer la telemetría cada segundo y el GPS a 5 Hz en cuanto
arranque el nodo (o el simulador). Para publicar algo a mano y ver que la ACL
funciona:

```bat
mosquitto_pub -h 127.0.0.1 -u servidor -P ucsm2026servidor -t "ucsm/v1/NODO-01/cmd" -m "{\"accion\":\"recalibrar_piso\"}"
```

Si en vez de datos ves `Connection Refused: not authorised`, revisa el usuario,
la clave y el archivo `acl`.

---

## 6. Contrato de tópicos (fijo)

Prefijo: `ucsm/v1/<codigo>` donde `<codigo>` es `dispositivo.codigo` (ej. `NODO-01`).

| Tópico | Publica | QoS | Retenido | Contenido |
|---|---|---|---|---|
| `ucsm/v1/NODO-01/telemetria` | nodo | 0 | no | JSON completo cada 1 s |
| `ucsm/v1/NODO-01/gps` | nodo | 0 | no | Posición: 1 Hz estacionado, **5 Hz en marcha** |
| `ucsm/v1/NODO-01/evento` | nodo | **1** | no | Eventos de las reglas, con `evento_uid` |
| `ucsm/v1/NODO-01/estado` | nodo / *Last Will* | 1 | **sí** | `{"en_linea":true,...}`; LWT = `{"en_linea":false}` |
| `ucsm/v1/NODO-01/modo` | nodo | 1 | **sí** | `{"modo":"ESTACIONADO","fuente":"INERCIAL","ts_ms":...}` |
| `ucsm/v1/NODO-01/config` | servidor | 1 | **sí** | `{"armado":true,"umbrales":{...}}` |
| `ucsm/v1/NODO-01/cmd` | servidor | 1 | no | `{"accion":"reiniciar"\|"recalibrar_piso"\|"forzar_modo"}` |
| `ucsm/v1/NODO-01/sync/req` | nodo | 0 | no | `{"id":17,"t0":123456}` (ms monotónicos del nodo) |
| `ucsm/v1/NODO-01/sync/resp` | servidor | 0 | no | `{"id":17,"t0":123456,"server_ts_ms":1760330000123}` |
| `ucsm/v1/NODO-01/cam/estado` | cámara / *Last Will* | 1 | **sí** | Estado de la ESP32-CAM; LWT = `{"en_linea":false}` |
| `ucsm/v1/NODO-01/cam/cmd` | servidor | 1 | no | `{"resolucion":"VGA","calidad":12,"fps_max":8,"flash":false}` |

**Client IDs:** `nodo-NODO-01`, `cam-NODO-01`, `servidor-ucsm`.
`clean_session = true`, keepalive 15 s.

### Por qué cada QoS

- **QoS 0** en telemetría y GPS: son datos que se repiten (1 Hz y 5 Hz). Si se
  pierde uno, el siguiente llega en milisegundos; reintentar sería peor.
- **QoS 1** en eventos: un evento perdido es una alerta perdida. QoS 1 puede
  entregar el mismo mensaje **dos veces**, y por eso el payload lleva
  `evento_uid`: el servidor deduplica con él (`ProcesadorEventos`).
- **Retenido** en `estado`, `modo`, `config` y `cam/estado`: son *estados*, no
  eventos. El último valor siempre es el válido y el broker lo entrega a quien
  se suscriba después.

---

## 7. Sincronización de reloj (`medicion_tiempo`)

El capítulo de resultados mide latencias entre el instante del sensor y la
confirmación de Telegram. Para que esa resta tenga sentido, los dos relojes
tienen que estar alineados:

```
nodo                                          servidor
 │  sync/req  {"id":17,"t0":123456}  ───────────►│   t_recepcion_ms (lo PRIMERO que hace)
 │                                              │
 │◄────── sync/resp {"id":17,"t0":123456,        │
 │                   "server_ts_ms":1760330000123}
 │
 │  t1 = ahora (monotónico)
 │  rtt    = t1 − t0
 │  offset = server_ts_ms − (t0 + t1) / 2
```

- Se publica `sync/req` cada **30 s**, y **5 veces seguidas** al conectar.
- Se conserva la muestra de **menor RTT** dentro de una ventana de 60 s: la
  muestra más rápida es la menos contaminada por la cola de la red.
- El servidor responde **sin tocar la base de datos**: cualquier consulta
  añadiría milisegundos al RTT y falsearía el offset.
- Las reglas del firmware trabajan siempre con el reloj **monotónico** (nunca
  salta); el instante solo se convierte a época al publicar el evento. Así un
  ajuste de reloj no deforma las ventanas de 500 ms de las reglas.
- Respaldo: NTP. Si no hay ninguna fuente, `ts_ms` se publica como `null` y el
  servidor usa su hora de recepción (queda anotado en `observacion`).

El RTT vigente se ve en el Monitor Serie (`rtt_sync`) y en la telemetría
(`mqtt.rtt_sync_ms`).

---

## 8. Librerías del firmware

### Nodo ESP32-S3 (`firmware\nodo_iot_esp32s3\`)

| Librería | Dónde se obtiene | ¿Obligatoria? |
|---|---|---|
| **esp-mqtt** (`mqtt_client.h`) | Viene en el core **arduino-esp32 3.x** | Sí |
| **TinyGPSPlus** (Mikal Hart) | Gestor de librerías del IDE | Sí |
| **ArduinoJson 7.x** (Benoit Blanchon) | Gestor de librerías del IDE | Sí |
| **esp-dsp** (`esp_dsp.h`) | <https://github.com/espressif/esp-dsp> (ZIP → *Incluir librería → Añadir biblioteca .ZIP*) | **No**: si falta, se usa `TransformadaRadix2`, la FFT interna del proyecto |
| **PubSubClient** | Solo si tu core no trae `mqtt_client.h` | No |

Configuración de la placa en el Arduino IDE:

- Placa: **ESP32S3 Dev Module**
- **USB CDC On Boot: Enabled** (si no, no verás el Monitor Serie)
- PSRAM: según tu módulo (el nodo no la necesita)

> **Si tu core no trae `mqtt_client.h`:** reemplaza la clase `ClienteMQTT` por
> `PubSubClient` con `setBufferSize(2048)`, en una tarea dedicada del núcleo 0,
> manteniendo la misma interfaz pública (`publicarEvento`, `publicarDato`,
> `publicarRetenido`, `publicarSync`). Déjalo documentado en el informe.

### Cámara ESP32-CAM (`firmware\esp32cam_camara\`)

| Librería | Dónde se obtiene |
|---|---|
| **esp_camera** y **esp_http_server** | Vienen en el core arduino-esp32 3.x |
| **esp-mqtt** (`mqtt_client.h`) | Viene en el core |
| **ArduinoJson 7.x** | Gestor de librerías del IDE |

Configuración de la placa: **AI Thinker ESP32-CAM**.
**Alimentación: 5 V / 2 A estable.** Casi todos los reinicios aleatorios de la
ESP32-CAM son por una fuente débil, no por el código.

---

## 9. Qué editar en el bloque `cfg` de cada sketch

### `nodo_iot_esp32s3.ino`

```cpp
const char* WIFI_SSID = "Papitaa";              // tu red Wi-Fi
const char* WIFI_PASS = "person123";

const char* MQTT_HOST    = "10.144.55.103";     // IP de la PC: "ipconfig" en Windows
const char* MQTT_USUARIO = "nodo01";
const char* MQTT_CLAVE   = "ucsm2026nodo";      // la de infra\mosquitto\passwd
const char* PREFIJO_TOPICO = "ucsm/v1";         // = MQTT_PREFIJO en .env

const char* CODIGO_DISPOSITIVO = "NODO-01";     // debe existir en la tabla dispositivo

constexpr bool USAR_CONTACTO = false;           // true si conectas el ACC/IGN
constexpr bool USAR_BATERIA  = true;            // false si no pusiste el divisor
```

### `esp32cam_camara.ino`

```cpp
const char* MQTT_HOST   = "10.144.55.103";      // la misma IP de la PC
const char* MQTT_USUARIO = "cam01";
const char* MQTT_CLAVE  = "ucsm2026cam";
const char* CODIGO_NODO = "NODO-01";            // el nodo al que pertenece la cámara

constexpr bool USAR_IP_FIJA = true;             // recomendado: config.py apunta a esta IP
const IPAddress IP_FIJA(10, 144, 55, 120);
const IPAddress GATEWAY(10, 144, 55, 1);
```

La IP fija de la cámara tiene que coincidir con `.env`:

```ini
ESP32CAM_URLS=NODO-01=http://10.144.55.120
ESP32CAM_STREAM_URLS=NODO-01=http://10.144.55.120:81/stream
```

---

## 10. Señal de contacto (ACC/IGN) con PC817

El modo del vehículo (`EN_MARCHA` / `ESTACIONADO`) se puede deducir del
acelerómetro (por defecto) o leer directamente del contacto del vehículo, que
es más fiable e inmediato. El ACC son **12 V** y el ESP32-S3 trabaja a **3.3 V**:
nunca lo conectes directo.

### Opción recomendada: optoacoplador PC817 (aislamiento galvánico)

```
   ACC +12 V ──[ R 1 kΩ ]──► (1) ánodo
                                     ┌─────────────┐
                                     │   PC817     │
   GND vehículo ◄──────────── (2) cátodo          │
                                     │             │
   GPIO10 del ESP32 ◄──────── (4) colector        │
                                     │             │
   GND del ESP32 ◄─────────── (3) emisor          │
                                     └─────────────┘

   GPIO10 configurado como INPUT_PULLUP
```

Con el contacto **encendido** el LED interno del PC817 conduce, el
fototransistor satura y GPIO10 lee **LOW**. Por eso:

```cpp
constexpr bool USAR_CONTACTO        = true;
constexpr int  PIN_CONTACTO         = 10;
constexpr bool CONTACTO_ACTIVO_BAJO = true;     // PC817
```

La resistencia de 1 kΩ limita la corriente del LED a ≈ 11 mA a 12 V, dentro del
máximo del PC817 (50 mA) y suficiente para saturarlo.

### Opción simple: divisor resistivo + zener de 3.3 V

```
   ACC +12 V ──[ R 10 kΩ ]──┬──[ R 3.3 kΩ ]── GND
                            │
                            ├──|◄|── GND          (zener 3.3 V, recorte)
                            │
                            └──────► GPIO10        (INPUT)
```

El divisor da ≈ 2.97 V con 12 V de entrada, y el zener recorta los picos del
alternador (que pueden pasar de 14 V). Aquí el pin lee **HIGH** con el contacto
encendido, así que pon `CONTACTO_ACTIVO_BAJO = false`.

> Esta opción **no aísla** el ESP32 de la electrónica del vehículo. Para la
> instalación definitiva usa el PC817.

En ambos casos la señal lleva un **antirrebote de 2 s** en `MaquinaModo`, y
cuando está habilitada **tiene prioridad** sobre la regla inercial (`fuente`
pasa de `INERCIAL` a `CONTACTO` en el tópico `modo`).

---

## 11. Reglas de detección y sus umbrales

Las reglas viven en `firmware\nodo_iot_esp32s3\reglas_deteccion.h`, sin
dependencias de hardware, y se prueban en la PC con
`firmware\pruebas\compilar_pruebas.bat`.

### Configuración del sensado

| Parámetro | Valor | Por qué |
|---|---|---|
| MPU-6050 rango | **±16 g** (`ACCEL_CONFIG = 0x18`, 2048 LSB/g) | A ±4 g un choque real satura el sensor |
| MPU-6050 muestreo | **500 Hz** (`SMPLRT_DIV = 1`) | Un impacto dura pocos ms |
| MPU-6050 filtro | `CONFIG = 0x01` (≈184 Hz) | No recortar los picos de impacto |
| Giroscopio | ±500 °/s (65.5 LSB/°/s) | — |
| INMP441 | **16 kHz**, bloques de **256 muestras (16 ms)** | 62.5 Hz por bin de FFT |
| FFT | 256 puntos, ventana de **Hann** | Resolución suficiente para separar las dos bandas |
| Banda baja | **150–400 Hz** → bins 3–6 | El "golpe" sordo del impacto en el vidrio |
| Banda alta | **3–7 kHz** → bins 48–112 | El "estallido" del vidrio templado |
| dB SPL | `120 + 20·log10(pico / 2^23)` | Calibración del INMP441 (−26 dBFS @ 94 dB SPL) |
| Vibración | `| |a| − media_móvil(|a|) |` | La media móvil exponencial elimina la gravedad |
| Planitud espectral | media geométrica / media aritmética de `|X[k]|²` | Distingue ruido de banda ancha (vidrio, ≈1) de un tono puro (bocina, ≈0) |
| Piso de ruido | Se aprende solo: 2 s de inicialización y después media móvil con τ ≈ 10 s | Solo se actualiza si el bloque está por debajo de `piso + 6 dB` y no hay detección en curso, para no "aprenderse" el evento |

### Tabla de reglas (lista para la tesis)

| Regla | Modo | Armado | Condición | Evento | Nivel | Acción del servidor |
|---|---|---|---|---|---|---|
| 1 | `EN_MARCHA` | indiferente | `|a| ≥ 4 g` **Y** `sonido ≥ 112 dB SPL` **Y** `|t_a − t_s| ≤ 500 ms` | `CHOQUE` | ALERTA | Incidencia + alerta de Telegram con foto a todos |
| 1 | `EN_MARCHA` | indiferente | `|a| ≥ 4 g` sin sonido en ±500 ms | `IMPACTO_SIN_CONFIRMAR` | REGISTRO | Solo incidencia |
| 1 | `EN_MARCHA` | indiferente | Solo sonido fuerte | — | — | Se ignora (solo log serie) |
| 2 | `ESTACIONADO` | **sí** | Golpe (`energía 150–400 Hz > piso + 15 dB` **o** `vibración > 0.15 g`) → estallido en ≤300 ms (`energía 3–7 kHz > piso + 12 dB` **Y** `planitud ≥ 0.3`) sostenido ≥150 ms, **con** vibración en ±500 ms | `ROTURA_VENTANA` | ALERTA | Incidencia + alerta con foto a todos |
| 2 | `ESTACIONADO` | **sí** | Lo mismo pero **sin** vibración | `VIDRIO_ROTO_EXTERNO` | REGISTRO | Solo incidencia (se rompió algo cerca, no el auto) |
| 2 | `ESTACIONADO` | **sí** | `vibración > 0.15 g` sin estallido en 1 s | `GOLPE_MANIPULACION` | ALERTA_LEVE | Incidencia + alerta **silenciosa** |
| YOLO | `ESTACIONADO` | **sí** | Persona confirmada en N cuadros seguidos | `PERSONA` | ALERTA | Incidencia + alerta con foto |

Antirrebote: un evento de la regla 1 cada **10 s**; uno de la regla 2 cada **15 s**.

> **Por qué CHOQUE se evalúa aunque esté desarmado:** un choque es una
> emergencia, no un robo. **Por qué PERSONA solo se alerta estacionado:** en
> marcha siempre hay personas dentro del auto, y toda alerta sería un falso
> positivo. En marcha el vídeo se sigue viendo y anotando, sin alertar.

### Modo del vehículo

| Fuente | Condición | Resultado |
|---|---|---|
| `CONTACTO` | Contacto encendido 2 s seguidos | `EN_MARCHA` |
| `CONTACTO` | Contacto apagado 2 s seguidos | `ESTACIONADO` |
| `INERCIAL` | `max(|a|) − min(|a|)` de la ventana de 1 s **< 0.05 g** durante **300 s** | `ESTACIONADO` |
| `INERCIAL` | La misma variación **≥ 0.05 g** durante **10 s** | `EN_MARCHA` |
| `FORZADO` | Comando `{"accion":"forzar_modo","modo":"..."}` | El indicado (pruebas) |

Arranca siempre en `EN_MARCHA`.

### Umbrales configurables desde el servidor

Los 14 umbrales se publican en `config` (retenido) y el nodo los aplica en
caliente. Se editan en `.env` (prefijo `DET_`) y se ven en la página
**Configuración**:

```json
{"armado": true,
 "umbrales": {"choque_g": 4.0, "choque_db": 112, "choque_ventana_ms": 500,
              "golpe_baja_db": 15, "estallido_alta_db": 12, "planitud_min": 0.3,
              "estallido_ventana_ms": 300, "estallido_min_ms": 150, "fusion_ms": 500,
              "vibracion_g": 0.15, "golpe_sin_estallido_ms": 1000,
              "quieto_g": 0.05, "quieto_s": 300, "marcha_s": 10}}
```

---

## 12. GPS NEO-6M a 5 Hz

De fábrica el NEO-6M sale a 1 Hz, 9600 baudios y con las seis sentencias NMEA
activas. El firmware lo reconfigura por **UBX** al arrancar:

| Comando UBX | Clase/ID | Qué hace |
|---|---|---|
| `CFG-PRT` | `0x06 0x00` | Sube el UART del GPS a **38400 baudios** |
| `CFG-MSG` | `0x06 0x01` | Apaga GLL, GSA, GSV y VTG; deja **RMC** (posición, velocidad, **rumbo**) y **GGA** (satélites, HDOP, altitud) |
| `CFG-RATE` | `0x06 0x08` | `measRate = 200 ms` → **5 Hz** |

A 9600 baudios, 2 sentencias × 5 Hz entran muy justas (≈7500 de 9600 bps) y se
pierden tramas; de ahí el cambio a 38400. Tras subir la velocidad el firmware
**verifica** que lleguen tramas con checksum válido durante 1.5 s; si el módulo
no responde (clones con firmware distinto), **retrocede a 9600** y reconfigura
ahí. El Monitor Serie dice a qué velocidad quedó.

Reglas de publicación:

- **5 Hz** en `EN_MARCHA`, **1 Hz** en `ESTACIONADO`.
- Latitud y longitud con **7 decimales** (≈1 cm de resolución).
- No se publican posiciones con `hdop > 5`: se manda
  `{"fix": false, "motivo": "hdop"}` para no ensuciar el rastro del mapa.

---

## 13. Clave de Google Maps

Sin clave el mapa funciona igual con **Leaflet + OpenStreetMap** (no requiere
cuenta ni facturación). Con clave se usa la Maps JavaScript API, que se ve
mejor y permite "Abrir en Google Maps".

### Crear la clave

1. Entra en <https://console.cloud.google.com/> y crea un proyecto
   (ej. *Monitoreo UCSM*).
2. **APIs y servicios → Biblioteca**, y habilita:
   - **Maps JavaScript API** (el mapa del panel y de la página "Mapa en vivo");
   - **Maps Static API** (miniaturas de los mapas);
   - **Geocoding API** solo si vas a activar `GOOGLE_GEOCODING=True`.
3. **Facturación:** Google exige una tarjeta incluso dentro de la cuota
   gratuita. Google Maps Platform da un crédito mensual gratuito que cubre de
   sobra el uso de una tesis (unos pocos miles de cargas de mapa). Para dormir
   tranquilo, pon un **presupuesto con alerta** en *Facturación → Presupuestos*
   y una **cuota diaria** en *APIs y servicios → Cuotas*.
4. **APIs y servicios → Credenciales → Crear credenciales → Clave de API**.

### Restringir la clave (importante)

Una clave sin restricciones que aparezca en el HTML la puede usar cualquiera.

1. En la clave, **Restricciones de aplicación → Sitios web** (referente HTTP).
2. Agrega los referentes de tu despliegue en la LAN:

   ```
   http://127.0.0.1:8000/*
   http://localhost:8000/*
   http://10.144.55.103:8000/*     ← la IP de tu PC
   ```

3. En **Restricciones de API**, deja solo las APIs de los pasos anteriores.

Si pones la IP de la PC, recuerda que cambia al cambiar de red Wi-Fi: añade
todas las que uses, o reserva una IP fija en el router.

### Configurar en el proyecto

```ini
GOOGLE_MAPS_API_KEY=AIza...
GOOGLE_GEOCODING=False
MAPA_ZOOM=16
```

Si Google rechaza la clave (facturación sin activar, referente no permitido),
la página lo dice en el propio contenedor del mapa y en la consola del
navegador (`gm_authFailure`), y cae al respaldo de OpenStreetMap.

---

## 14. Vídeo: stream MJPEG y buffer de 3 s

El firmware de la cámara expone **dos** servidores HTTP:

| Puerto | Rutas | Autorización |
|---|---|---|
| **80** | `/capture`, `/status`, `/config` | Cabecera `X-API-Key` o `?key=` |
| **81** | `/stream` (MJPEG, un solo cliente) | `?key=` |

El worker `ReceptorStreamCamara` mantiene abierto `/stream` y guarda los
últimos **3 s** de cuadros en memoria (`BufferCuadros`). A partir de ahí:

- `VigilanciaVisual` toma siempre el cuadro **más reciente** para YOLO;
- `IncidenciaService` toma el cuadro **más cercano a `t_deteccion_sensor`**, es
  decir la imagen del instante del golpe;
- si no hay stream, todo cae a `/capture` y queda anotado en `observacion` de la
  incidencia qué método se usó (`"Imagen del stream MJPEG, a 37 ms del instante
  del sensor."` frente a `"Imagen pedida a /capture (sin stream disponible)."`).

`fps_max` (8 por defecto) limita los cuadros por segundo para no saturar el
Wi-Fi; se puede cambiar en caliente desde la página **Configuración**, que
publica `cam/cmd`.

---

## 15. Arrancar todo y comprobarlo

```bat
REM 1) Broker (deja esta consola abierta)
infra\mosquitto\iniciar_mosquitto.bat

REM 2) Servidor (otra consola)
.venv\Scripts\activate
python main.py

REM 3) Nodo simulado, sin hardware (otra consola)
python manage.py simular_nodo --codigo NODO-01 --segundos 120 --eventos 5
```

El banner de `main.py` dice si el broker está conectado. El simulador recorre
una ruta por Arequipa a 5 Hz y dispara los cinco tipos de evento con su
evidencia completa.

Opciones útiles del simulador:

| Opción | Para qué |
|---|---|
| `--duplicar-eventos` | Reenvía cada evento con el mismo `evento_uid`: comprueba la deduplicación de QoS 1 |
| `--http` | Usa la API HTTP de respaldo en vez de MQTT |
| `--eventos 0` | Solo telemetría y GPS (para ver el mapa moviéndose) |

### Pruebas

```bat
REM Reglas de detección en la PC, sin ESP32 (47 casos)
firmware\pruebas\compilar_pruebas.bat

REM Servidor completo (138 pruebas)
python manage.py test monitoreo
```

### Checklist de verificación en hardware

- [ ] El Monitor Serie del nodo muestra `MQTT: conectado`, el modo, el piso de
      ruido de las dos bandas, `FFT ... us / 16000 us` y `rtt_sync`.
- [ ] `mosquitto_sub -t "ucsm/v1/#" -v` muestra telemetría cada 1 s y GPS a 5 Hz
      cuando el vehículo se mueve.
- [ ] Al desenchufar el nodo, el panel lo marca fuera de línea en ≤ 22 s (LWT).
- [ ] Armar/desarmar desde la web o el bot aparece en el Monitor Serie del nodo
      como `[CONFIG] armado = ...`.
- [ ] El mapa del panel muestra el vehículo moviéndose, con flecha orientada y
      rastro.
- [ ] El vídeo del panel llega con las cajas de YOLO dibujadas.

---

## 16. Problemas frecuentes

| Síntoma | Causa probable | Solución |
|---|---|---|
| `Connection Refused: not authorised` en `mosquitto_sub` | Usuario/clave mal, o `acl` no permite el tópico | Revisa `infra\mosquitto\passwd` y `acl`; reinicia el broker |
| El banner dice `SIN conexión` al broker | Mosquitto no está corriendo, o `MQTT_HOST` apunta a otra IP | Arranca el `.bat`; con el servidor y el broker en la misma PC, `MQTT_HOST=127.0.0.1` |
| El nodo no conecta pero la PC sí | El nodo usa la **IP de la LAN**, no `127.0.0.1` | Pon la IP real de la PC (`ipconfig`) en `cfg::MQTT_HOST` |
| El nodo conecta y se cae en bucle | Dos clientes con el **mismo** client ID (p. ej. el nodo real y el simulador) | Para el simulador, o cambia `CODIGO_DISPOSITIVO` |
| `Mensaje MQTT de 'NODO-01' sin dispositivo registrado` | El código no existe en la tabla `dispositivo` | Créalo en **Dispositivos**, o corre `python manage.py sembrar_catalogos --demo` |
| Llegan eventos duplicados en el log | Normal: QoS 1 puede reentregar | El servidor los descarta por `evento_uid`; el contador se ve en Configuración |
| El piso de ruido no se estabiliza | Menos de 2 s desde el arranque, o ambiente muy cambiante | Espera 2 s; usa "Recalibrar piso de ruido" en Configuración |
| GPS sin fix dentro del edificio | El NEO-6M necesita cielo abierto | Saca la antena a una ventana; el primer fix en frío tarda 1–5 min |
| `[GPS] AVISO: no responde a 38400` | Clon del NEO-6M que no acepta `CFG-PRT` | No es un error: el firmware retrocede a 9600 y sigue funcionando |
| Reinicios aleatorios de la ESP32-CAM | Fuente de alimentación débil | 5 V / 2 A estables, cables cortos |
| El stream da 503 | Ya hay un cliente viendo `/stream` | Solo el servidor debe conectarse; cierra la pestaña del navegador que lo esté viendo |
| Mapa en blanco con clave configurada | Facturación sin activar o referente no permitido | Mira la consola del navegador (`gm_authFailure`) y revisa las restricciones |

---

## 17. Resumen de archivos

| Ruta | Qué contiene |
|---|---|
| `infra\mosquitto\` | Configuración, ACL y script de arranque del broker |
| `firmware\nodo_iot_esp32s3\nodo_iot_esp32s3.ino` | Nodo ESP32-S3 v3.0.0 (MQTT, reglas, GPS 5 Hz) |
| `firmware\nodo_iot_esp32s3\reglas_deteccion.h` | Lógica pura de las reglas (se prueba en la PC) |
| `firmware\esp32cam_camara\esp32cam_camara.ino` | Cámara v3.0.0 (puerto 80 + 81/stream, MQTT) |
| `firmware\pruebas\` | Pruebas de host de las reglas (`compilar_pruebas.bat`) |
| `monitoreo\services\mqtt_service.py` | `ClienteMqtt` y `PublicadorConfiguracion` |
| `monitoreo\workers\puente_mqtt.py` | `PuenteMQTT`: reparte cada tópico a su servicio |
| `monitoreo\workers\receptor_stream.py` | Lee el MJPEG y llena el buffer de 3 s |
| `monitoreo\services\buffer_cuadros.py` | Buffer circular de cuadros por cámara |
| `monitoreo\services\reglas_accion.py` | Política de acción por `nivel_riesgo` |
| `monitoreo\static\monitoreo\js\mapa_vivo.js` | Mapa en vivo (flecha, interpolación, rastro) |
