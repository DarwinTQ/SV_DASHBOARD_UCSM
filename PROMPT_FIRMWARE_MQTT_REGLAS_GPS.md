
## 0. Instrucciones para Claude Code

1. **Proyecto:** `D:\Tesis_2026\SV_INTERFAZ_UCSM\` (Django ya construido: `config.py`, `main.py` con `ServidorPrincipal`, app `monitoreo` con services/workers/controllers, bot de Telegram, Channels).
2. **Firmware a modificar EN SU MISMO ARCHIVO (no crear otros sketches):**
   - `D:\Tesis_2026\SV_INTERFAZ_UCSM\firmware\nodo_iot_esp32s3\nodo_iot_esp32s3.ino`
   - `D:\Tesis_2026\SV_INTERFAZ_UCSM\firmware\esp32cam_camara\esp32cam_camara.ino`
   Léelos completos antes de tocarlos. Conserva la estructura orientada a objetos (clases), el bloque `namespace cfg` con los datos de Wi-Fi y pines, los comentarios en español y la cabecera de documentación (actualízala a **v3.0.0**). Si una ruta no existe, búscala dentro del proyecto y avísame.
3. Todo en español (código, comentarios, mensajes). POO en firmware y servidor. Secretos solo en `config.py`/`.env` (servidor) y en el bloque `cfg` (firmware).
4. **Base de datos:** no cambies campos ni tablas existentes. Solo puedes **agregar filas** al catálogo `tipo_evento` (sección 5.4).
5. Trabaja por fases (sección 9). Al final de cada fase compila/prueba. Si `arduino-cli` está instalado, compila ambos sketches:
   `arduino-cli compile --fqbn esp32:esp32:esp32s3 firmware/nodo_iot_esp32s3` y `arduino-cli compile --fqbn esp32:esp32:esp32cam firmware/esp32cam_camara`. Si no está, dímelo y entrega igual las pruebas de host de la sección 6.
6. Entrega al final un resumen de cambios y la guía `docs/GUIA_MQTT.md` (sección 8).

---

## 1. Decisión de protocolos (justificación para la tesis)

| Flujo | Protocolo | Motivo |
|---|---|---|
| Nodo ESP32-S3 → servidor (telemetría, GPS, eventos, estado) | **MQTT** (broker Mosquitto local) | Conexión persistente, cabecera mínima (2 bytes), QoS por tipo de mensaje, *Last Will* para detectar desconexión al instante, comandos al nodo por suscripción |
| Servidor → nodo (armar/desarmar, umbrales, sincronización de reloj) | **MQTT** (mensajes retenidos) | El nodo recibe la configuración vigente apenas se conecta |
| ESP32-CAM → servidor (video) | **HTTP MJPEG** `/stream` (puerto 81) | Estándar y eficiente en ESP32-CAM; una sola conexión persistente; MQTT no sirve para video (payload grande, salto extra por el broker) |
| ESP32-CAM foto puntual | **HTTP** `/capture` (puerto 80) | Respaldo cuando el stream no está disponible |
| ESP32-CAM estado y comandos | **MQTT** (estado retenido + *Last Will*, `cam/cmd`) | Mismo canal de control que el nodo |
| Servidor → navegador (telemetría, mapa, video anotado) | **WebSocket** (Django Channels, ya existe) | Push en tiempo real, frames binarios |

Regla: **solo el servidor se conecta a la cámara**; navegador y Telegram reciben imágenes desde el servidor.

---

## 2. Tópicos MQTT (contrato fijo)

Prefijo: `ucsm/v1/<codigo>` donde `<codigo>` = `dispositivo.codigo` (ej. `NODO-01`).

| Tópico | Publica | QoS | Retenido | Contenido |
|---|---|---|---|---|
| `ucsm/v1/NODO-01/telemetria` | nodo | 0 | no | JSON completo cada 1 s (sección 3.6) |
| `ucsm/v1/NODO-01/gps` | nodo | 0 | no | Posición: 1 Hz ESTACIONADO, **5 Hz** EN MARCHA (sección 3.5) |
| `ucsm/v1/NODO-01/evento` | nodo | **1** | no | Eventos de las reglas (sección 3.4), con `evento_uid` |
| `ucsm/v1/NODO-01/estado` | nodo / *Last Will* | 1 | **sí** | `{"en_linea":true,...}`; LWT = `{"en_linea":false}` |
| `ucsm/v1/NODO-01/modo` | nodo | 1 | **sí** | `{"modo":"ESTACIONADO","fuente":"INERCIAL","ts_ms":...}` |
| `ucsm/v1/NODO-01/config` | servidor | 1 | **sí** | `{"armado":true,"umbrales":{...}}` |
| `ucsm/v1/NODO-01/cmd` | servidor | 1 | no | `{"accion":"reiniciar"}`, `{"accion":"recalibrar_piso"}`, `{"accion":"forzar_modo","modo":"EN_MARCHA"}` |
| `ucsm/v1/NODO-01/sync/req` | nodo | 0 | no | `{"id":17,"t0":123456}` (ms monotónicos del nodo) |
| `ucsm/v1/NODO-01/sync/resp` | servidor | 0 | no | `{"id":17,"t0":123456,"server_ts_ms":1760330000123}` |
| `ucsm/v1/NODO-01/cam/estado` | cámara / *Last Will* | 1 | **sí** | estado de la ESP32-CAM; LWT = `{"en_linea":false}` |
| `ucsm/v1/NODO-01/cam/cmd` | servidor | 1 | no | `{"resolucion":"VGA","calidad":12,"fps_max":8,"flash":false}` |

Client IDs: `nodo-NODO-01`, `cam-NODO-01`, `servidor-ucsm`. `clean_session=true`, keepalive 15 s.

---

## 3. FIRMWARE NODO ESP32-S3 (`nodo_iot_esp32s3.ino`)

### 3.1 Cliente MQTT
- Usar el cliente nativo **ESP-IDF `esp-mqtt`** (`#include "mqtt_client.h"`, incluido en el core arduino-esp32 3.x): asíncrono, en su propia tarea, QoS 1, LWT y buffer configurable (`buffer.size = 2048`, `out_buffer_size = 2048`). Si no compila en el core instalado, usar **PubSubClient** (`setBufferSize(2048)`) en una tarea dedicada en el núcleo 0 y documentarlo.
- Nueva clase `ClienteMQTT` que reemplaza a `ClienteAPI`: conexión con usuario/contraseña, LWT retenido en `estado`, al conectar publica `estado` retenido (`en_linea:true`, firmware, ip, mac, rssi, boot_id) y se suscribe a `config`, `cmd`, `sync/resp`. Reconexión automática con backoff; contador de reconexiones.
- Colas FreeRTOS separadas: eventos (prioridad, QoS 1) y telemetría/GPS (QoS 0, si la cola se llena se descarta lo más viejo).
- `cfg` nuevo: `MQTT_HOST` (IP de la PC), `MQTT_PUERTO=1883`, `MQTT_USUARIO="nodo01"`, `MQTT_CLAVE`, `PREFIJO_TOPICO="ucsm/v1"`.
- **Eliminar** el uso de HTTP del nodo (ya no se usa `/api/v1/telemetria/` ni `/api/v1/eventos/`).

### 3.2 Reloj sincronizado (por MQTT)
- Clase `RelojSincronizado` se mantiene, pero el ajuste ahora es por `sync/req` → `sync/resp`: cada 30 s (y 5 veces seguidas al conectar) publica `{"id","t0"}`; al recibir la respuesta con el mismo `id` calcula `rtt = t1 − t0` y `offset = server_ts_ms − (t0 + t1)/2`, quedándose con la muestra de **menor RTT** en una ventana de 60 s. Respaldo NTP como antes. Todos los `ts_ms` de eventos, GPS y telemetría usan este reloj.

### 3.3 Modo del vehículo (clase `MaquinaModo`)
Estados: `EN_MARCHA` y `ESTACIONADO`. Arranque en `EN_MARCHA`.
- **Con señal de contacto** (`cfg::USAR_CONTACTO = false` por defecto; `cfg::PIN_CONTACTO = 10`, entrada desde el ACC/IGN de 12 V a través de optoacoplador PC817 o divisor + zener 3.3 V; documentar en comentarios): contacto encendido → `EN_MARCHA`, apagado → `ESTACIONADO`, con antirrebote de 2 s. Si está habilitada, **tiene prioridad** sobre la regla inercial (`fuente:"CONTACTO"`).
- **Sin señal de contacto** (`fuente:"INERCIAL"`), por ventanas de 1 s del MPU:
  - Variación de la ventana = `max(|a|) − min(|a|)`.
  - Si la variación es **< 0.05 g durante 300 s seguidos** (5 min) → `ESTACIONADO`.
  - Si la variación es **≥ 0.05 g durante 10 s seguidos** → `EN_MARCHA`.
- Cada cambio publica `modo` (retenido) y se incluye en la telemetría. Comando `forzar_modo` para pruebas.

### 3.4 Reglas definitivas de detección
Reemplazan a las anteriores. **Se eliminan** los eventos `VIBRACION` y `RUIDO` simples. `MANIPULACION` por inclinación y `MOVIMIENTO` por GPS quedan **desactivados por defecto** (`cfg::REGLAS_LEGADO = false`) para respetar estas reglas.

Implementa la lógica de reglas en **clases puras sin dependencias de hardware** (reciben muestras/bloques y timestamps; devuelven decisiones), para poder probarlas en PC (sección 6): `ReglaChoque`, `AnalizadorEspectral`, `EstimadorPiso`, `ReglaRotura`.

#### Sensor y audio (cambios de configuración)
- **MPU-6050 a ±16 g** (`ACCEL_CONFIG = 0x18`, **2048 LSB/g**). Muestreo **500 Hz** (`SMPLRT_DIV = 1`) con filtro `CONFIG = 0x01` (~184 Hz) para no recortar picos de impacto. Giroscopio igual (±500 °/s).
- **Vibración** = `| |a| − media_móvil(|a|) |` (la media móvil exponencial elimina la gravedad).
- **INMP441 a 16 kHz, bloques de 256 muestras (16 ms)**. Por bloque: `dBFS_pico = 20·log10(pico/2^23)` y **`dB SPL = 120 + dBFS_pico`**.
- **FFT de 256 puntos** por bloque con ventana de Hann. Usar **esp-dsp** (`#include "esp_dsp.h"`, `dsps_fft2r_fc32`, optimizado para ESP32-S3); si no está disponible, `arduinoFFT` v2. Resolución 62.5 Hz/bin:
  - Banda baja **150–400 Hz** → bins 3 a 6.
  - Banda alta **3–7 kHz** → bins 48 a 112.
  - Energía de banda (dB) = `10·log10(Σ|X[k]|² + ε)`.
  - **Planitud espectral** (banda alta) = media geométrica / media aritmética de `|X[k]|²` (0 a 1).
- **Piso de ruido** (`EstimadorPiso`, uno por banda): se aprende solo. Inicialización con los primeros 2 s; luego media móvil exponencial con constante de tiempo ~10 s, **actualizada solo si** el bloque está por debajo de `piso + 6 dB` y no hay una detección en curso (para no aprender los eventos). Comando `recalibrar_piso`. Se reporta en la telemetría.

#### Regla 1 — CHOQUE (solo en `EN_MARCHA`; se evalúa aunque esté desarmado, por seguridad)
```
SI |a| ≥ 4 g  Y  sonido ≥ 112 dB SPL  Y  |t_a − t_s| ≤ 500 ms  → evento CHOQUE                 (nivel ALERTA)
SI solo |a| ≥ 4 g (no hubo sonido en ±500 ms)                  → evento IMPACTO_SIN_CONFIRMAR  (nivel REGISTRO)
SI solo sonido fuerte                                          → se ignora (solo log serie)
```
- `t_a` = instante de la primera muestra con |a| ≥ 4 g (el pico se sigue midiendo 100 ms más para reportar `a_pico_g`).
- `t_s` = instante del primer bloque de audio con ≥ 112 dB SPL.
- El sonido puede llegar antes o después del golpe: guardar los últimos timestamps de ambos y decidir cuando se cumple la condición o cuando vence la ventana de 500 ms después de `t_a`.
- Antirrebote: un CHOQUE/IMPACTO cada 10 s como máximo.

#### Regla 2 — ROTURA DE VENTANA (solo en `ESTACIONADO` y **armado**)
Máquina de estados `ReglaRotura`:
```
1. GOLPE:      energía 150–400 Hz > piso_baja + 15 dB   (o vibración > 0.15 g)        → guarda t_golpe
2. ESTALLIDO:  en ≤ 300 ms desde t_golpe:  energía 3–7 kHz > piso_alta + 12 dB  Y  planitud ≥ 0.3
3. DURACIÓN:   estallido sostenido ≥ 150 ms  (≥ 10 bloques de 16 ms; tolera 1 bloque aislado sin cumplir)
4. FUSIÓN:     hubo vibración > 0.15 g del MPU dentro de ±500 ms de t_golpe → evento ROTURA_VENTANA   (nivel ALERTA)
               sin vibración                                                 → evento VIDRIO_ROTO_EXTERNO (nivel REGISTRO)
   Vibración > 0.15 g sin estallido en 1 s                                   → evento GOLPE_MANIPULACION  (nivel ALERTA_LEVE)
```
- Si el GOLPE fue solo acústico y no hay estallido en 300 ms → se descarta sin evento.
- La tarea de audio necesita saber las vibraciones del MPU: la tarea IMU guarda en un buffer circular protegido (portMUX) los timestamps de las últimas vibraciones > 0.15 g.
- Antirrebote: un evento de esta regla cada 15 s como máximo.

#### Tareas FreeRTOS
- IMU 500 Hz (núcleo 1, prioridad 4) · Audio + FFT (núcleo 1, prioridad 3) · GPS y orquestación en `loop()` · MQTT en núcleo 0.
- Medir y reportar en la telemetría el tiempo de CPU de la FFT por bloque (µs) para demostrar que cabe en 16 ms.

#### Payload de evento (`.../evento`, QoS 1)
```json
{"codigo":"NODO-01","evento_uid":"ABCD1234-7","tipo":"ROTURA_VENTANA","nivel":"ALERTA",
 "ts_ms":1760330000113,"ts_fuente":"SERVIDOR","modo":"ESTACIONADO","armado":true,
 "evidencia":{"e_baja_db":-38.2,"piso_baja_db":-55.0,"e_alta_db":-41.0,"piso_alta_db":-56.3,
              "planitud":0.42,"duracion_ms":208,"vib_g":0.31,"delta_vib_ms":-40},
 "aceleracion_g":1.31,"nivel_audio_db":104.7,
 "gps":{"fix":true,"lat":-16.3989123,"lon":-71.5369871,"vel_kmh":0.0,"rumbo_deg":0,"sats":8,"hdop":0.9}}
```
Para `CHOQUE`/`IMPACTO_SIN_CONFIRMAR` la evidencia es `{"a_pico_g":6.8,"spl_pico_db":115.2,"delta_ms":42}` (`delta_ms` null si no hubo sonido).
`nivel` ∈ {`ALERTA`, `ALERTA_LEVE`, `REGISTRO`}.

### 3.5 GPS en tiempo real (`.../gps`)
- **NEO-6M a 5 Hz**: al arrancar enviar los comandos UBX `CFG-RATE` (measRate = 200 ms) y desactivar sentencias NMEA innecesarias (dejar RMC y GGA) con `CFG-MSG`; subir el UART a **38400 baudios** con `CFG-PRT` (con verificación y retroceso a 9600 si no responde). Documentar en comentarios.
- Publicar `gps` a **5 Hz en EN_MARCHA** y **1 Hz en ESTACIONADO** (configurable).
- Payload:
  ```json
  {"ts_ms":1760330000113,"fix":true,"lat":-16.3989123,"lon":-71.5369871,"alt_m":2335.2,
   "vel_kmh":32.4,"rumbo_deg":118.5,"sats":8,"hdop":0.9,"edad_ms":120,"modo":"EN_MARCHA"}
  ```
  Latitud/longitud con **7 decimales** (`double`, de `TinyGPSPlus`), rumbo con `gps.course.deg()`.
- Sin fix: `{"ts_ms":...,"fix":false,"sats":3}`. No publicar posiciones con `hdop > 5`, marcándolas como `"fix":false,"motivo":"hdop"`.

### 3.6 Telemetría (`.../telemetria`, 1 s)
Mantener el JSON actual (codigo, firmware, mac, ip, boot_id, seq, uptime_ms, ts_ms, ts_fuente, armado, wifi, sistema, gps, imu, audio, bateria) **y agregar**:
`"modo"`, `"modo_fuente"`, `"contacto"` (bool o null), en `imu`: `a_max_g` (ahora hasta 16 g), `vib_max_g`, `variacion_g`; en `audio`: `spl_pico_db`, `piso_baja_db`, `piso_alta_db`, `fft_us`; y un bloque `"mqtt":{"reconexiones":0,"publicados":120,"descartados":0,"rtt_sync_ms":9}`. Verificar que quepa en 2048 bytes.

### 3.7 Configuración remota (`.../config`, retenido)
`{"armado":true,"umbrales":{"choque_g":4.0,"choque_db":112,"choque_ventana_ms":500,"golpe_baja_db":15,"estallido_alta_db":12,"planitud_min":0.3,"estallido_ventana_ms":300,"estallido_min_ms":150,"fusion_ms":500,"vibracion_g":0.15,"golpe_sin_estallido_ms":1000,"quieto_g":0.05,"quieto_s":300,"marcha_s":10}}`
Todos los valores tienen por defecto los de las reglas definitivas en `cfg` y se pueden sobrescribir desde el servidor.

---

## 4. FIRMWARE ESP32-CAM (`esp32cam_camara.ino`)
- Reemplazar `WebServer` por **`esp_http_server`** (como el ejemplo oficial CameraWebServer): servidor en **puerto 80** con `/capture`, `/status`, `/config` (se mantienen igual, con `X-API-Key` o `?key=`) y un **segundo servidor en puerto 81** con **`/stream`** (MJPEG `multipart/x-mixed-replace`), protegido con `?key=`. Un solo cliente de stream a la vez (si entra otro, 503). Cabecera por frame `X-Timestamp-Ms` (uptime).
- `fps_max` configurable (por defecto **8**) para no saturar el Wi-Fi; resolución por defecto VGA, calidad 12, `CAMERA_GRAB_LATEST`, 2 buffers en PSRAM.
- **MQTT** (mismo cliente esp-mqtt): publica `cam/estado` retenido al conectar y cada 30 s (`en_linea`, ip, mac, rssi, resolución, calidad, fps_real, clientes_stream, ram/psram libre, capturas, fallos) con LWT `{"en_linea":false}`; se suscribe a `cam/cmd` (resolución, calidad, fps_max, flash, reiniciar).
- Conservar IP fija opcional, desactivación del brownout y la estructura de clases (`Camara`, `ServidorAPI` → separar en `ServidorCaptura` y `ServidorStream`, `ClienteMQTTCamara`, `NodoCamara`).

---

## 5. SERVIDOR DJANGO (para que el dashboard siga funcionando con MQTT)

### 5.1 Broker Mosquitto (Windows)
- Documentar instalación (mosquitto.org, servicio de Windows) y crear `infra/mosquitto/mosquitto.conf`:
  ```
  listener 1883 0.0.0.0
  allow_anonymous false
  password_file D:\Tesis_2026\SV_INTERFAZ_UCSM\infra\mosquitto\passwd
  acl_file D:\Tesis_2026\SV_INTERFAZ_UCSM\infra\mosquitto\acl
  persistence true
  ```
- Usuarios `nodo01`, `cam01`, `servidor` (`mosquitto_passwd`) y `acl` que limite cada usuario a sus tópicos (`ucsm/v1/NODO-01/#` para el nodo y la cámara; `ucsm/v1/#` para el servidor).
- Script `infra/mosquitto/iniciar_mosquitto.bat` y regla de firewall para el puerto 1883.

### 5.2 Configuración (`config.py` + `.env.example`)
Nuevo grupo `MqttConfig`: `MQTT_HOST=127.0.0.1`, `MQTT_PUERTO=1883`, `MQTT_USUARIO=servidor`, `MQTT_CLAVE`, `MQTT_PREFIJO=ucsm/v1`, `MQTT_KEEPALIVE=15`. Nuevo grupo `UmbralesDeteccionConfig` con todos los umbrales de la sección 3.7 (se publican en `config`). En `Esp32CamConfig` agregar `ESP32CAM_STREAM_URLS=NODO-01=http://10.144.55.120:81/stream` y `ESP32CAM_FPS_MAX=8`. Agregar `paho-mqtt>=2` a `requirements.txt`.

### 5.3 Worker `PuenteMQTT` (lo arranca `main.py`)
- Cliente `paho-mqtt` v2 (`CallbackAPIVersion.VERSION2`), reconexión automática, suscrito a `ucsm/v1/+/telemetria|gps|evento|estado|modo|sync/req|cam/estado`.
- **Lo primero en `on_message`**: tomar `t_recepcion_ms = time.time_ns()//1_000_000`.
- `sync/req` → responder **de inmediato** en `sync/resp` (sin tocar la BD).
- `telemetria` → `TelemetriaService.procesar(...)` (misma lógica que antes: actualiza dispositivo, `EstadoEnVivo`, `estado_dispositivo` cada N s, WebSocket). Adaptar el servicio para aceptar los campos nuevos.
- `gps` → `EstadoEnVivo.actualizar_gps()` (rastro de los últimos 300 puntos en memoria) + WebSocket `gps` (sección 5.6) + `logs/gps_track.csv` opcional.
- `evento` → deduplicar por `evento_uid` y encolar en `ProcesadorEventos` (pipeline existente: frame → YOLO → BD → Telegram → `MedicionTiempo`).
- `estado` (incluido el LWT) → en línea/fuera de línea **inmediato** del dispositivo (reemplaza la detección por timeout, que queda solo como respaldo) + fila en `estado_dispositivo`.
- `modo` → `EstadoEnVivo.modo` + WebSocket `dispositivo_estado`.
- `cam/estado` → estado de la cámara en `EstadoEnVivo`, `dispositivo.mac_esp32cam`.
- Publicar `config` **retenido** al arrancar y cada vez que cambie `armado` o los umbrales (web, bot `/armar` `/desarmar`, página Configuración) mediante un servicio `PublicadorConfiguracion`.
- Los endpoints HTTP `/api/v1/telemetria/` y `/api/v1/eventos/` se **mantienen** como respaldo (no se borran).

### 5.4 Catálogo y reglas de alerta (sin cambiar la BD)
Agregar a `sembrar_catalogos` (con `update_or_create`):

| codigo | nombre | nivel_riesgo | Acción |
|---|---|---|---|
| CHOQUE | Choque detectado | ALTO | Incidencia + alerta Telegram a todos |
| ROTURA_VENTANA | Rotura de ventana | ALTO | Incidencia + alerta Telegram a todos |
| GOLPE_MANIPULACION | Golpe / manipulación | MEDIO | Incidencia + alerta **silenciosa** (`disable_notification=true`) |
| IMPACTO_SIN_CONFIRMAR | Impacto sin confirmar | BAJO | Solo incidencia (registro, sin Telegram) |
| VIDRIO_ROTO_EXTERNO | Vidrio roto externo | BAJO | Solo incidencia (registro, sin Telegram) |
| PERSONA | Persona detectada (YOLO) | ALTO | (ya existe) |

- La acción se decide por `tipo_evento.nivel_riesgo` (ALTO → alerta, MEDIO → alerta leve, BAJO → registro); el `nivel` que envía el firmware se valida contra eso y se registra en `observacion`.
- `incidencia.aceleracion_g` = `a_pico_g` o `aceleracion_g`; `incidencia.nivel_audio_db` = `spl_pico_db` o `nivel_audio_db`; la `evidencia` completa y el `modo` se guardan como texto legible en `observacion`.
- El texto de Telegram muestra la evidencia resumida (ej. "6.8 g + 115 dB en 42 ms", "estallido 208 ms, planitud 0.42").
- **YOLO / PERSONA:** `VigilanciaVisual` solo genera incidencias PERSONA cuando el modo es **ESTACIONADO** y el dispositivo está armado (en marcha siempre hay personas dentro del auto). En marcha el video se sigue mostrando, sin alertas.

### 5.5 Video: `ReceptorStreamCamara` (nuevo worker)
- Lee continuamente `ESP32CAM_STREAM_URLS` (MJPEG), separa los JPEG por boundary y guarda un **buffer circular de 3 s** con `t_recepcion` de cada frame. Reconexión automática.
- `VigilanciaVisual` toma el frame más reciente del buffer (ya no hace polling a `/capture`).
- `IncidenciaService` usa el frame del buffer **más cercano a `t_deteccion_sensor`** (sin esperar una captura nueva → menor tiempo de respuesta) y, si no hay stream, cae a `/capture`. Registrar en `observacion` qué método se usó.
- Al navegador se envían los frames **anotados** por WebSocket (binario, máx. 5 fps, solo si hay clientes viendo el panel).
- Publicar `cam/cmd` desde la página Configuración (resolución, calidad, fps_max, flash).

### 5.6 Mapa en tiempo real con Google Maps (dashboard)
- Mensaje WebSocket: `{"tipo":"gps","codigo":"NODO-01","data":{lat,lon,vel_kmh,rumbo_deg,hdop,sats,modo,ts_ms}}`.
- En el **Panel en tiempo real**, tarjeta **"Ubicación en vivo"** con **Google Maps JavaScript API** (`GOOGLE_MAPS_API_KEY`; si no hay clave, Leaflet + OSM):
  - Marcador del vehículo con ícono de flecha **rotado según `rumbo_deg`**, movimiento **interpolado suavemente** entre puntos (animación de ~200 ms, sin saltos).
  - **Rastro** (polyline) de los últimos 5 minutos; botón "Limpiar rastro".
  - **Círculo de precisión** aproximado (radio ≈ `hdop × 2.5 m`).
  - Botón **"Seguir vehículo"** (centra automáticamente) y botón **"Abrir en Google Maps"**.
  - Panel con lat/lon (7 decimales), velocidad, rumbo, satélites, HDOP, modo (EN MARCHA / ESTACIONADO) y antigüedad del dato ("hace 0.4 s"); si no llega GPS en 5 s, marcador atenuado y aviso "Sin señal GPS".
  - Marcadores de incidencias recientes en el mismo mapa.
  - Colores del sistema (verde `#0cb967` y blanco) para marcador, rastro y círculo.
- Página nueva **"Mapa en vivo"** en el menú (mapa a pantalla completa con el mismo comportamiento).
- Documentar en la guía cómo crear la clave en Google Cloud (Maps JavaScript API + Static Maps), activar facturación (cuota gratuita) y **restringirla por referente HTTP** a la IP local.
- El bot de Telegram `/ubicacion` usa el último punto de `EstadoEnVivo` (5 Hz en marcha).

---

## 6. Pruebas

### 6.1 Pruebas de host del firmware (sin hardware) — `firmware/pruebas/`
- Las clases puras (`ReglaChoque`, `AnalizadorEspectral`, `EstimadorPiso`, `ReglaRotura`, `MaquinaModo`) se extraen a `firmware/nodo_iot_esp32s3/reglas_deteccion.h` (**pestaña del mismo sketch**, sin dependencias de Arduino) e incluidas desde el `.ino`. Esta es la única excepción permitida a "un solo archivo", porque permite probar la lógica en PC.
- `firmware/pruebas/prueba_reglas.cpp` (g++ / MSVC, script `compilar_pruebas.bat`) con señales **sintéticas** a 16 kHz:
  1. Choque: pico 6 g + sonido 115 dB a 40 ms → CHOQUE; a 800 ms → IMPACTO_SIN_CONFIRMAR; solo sonido → nada.
  2. Rotura: golpe grave (200 Hz) + ruido blanco 3–7 kHz de 250 ms con vibración → ROTURA_VENTANA; sin vibración → VIDRIO_ROTO_EXTERNO; estallido de 80 ms → nada; tono puro de 5 kHz (planitud baja) → nada; vibración sola → GOLPE_MANIPULACION a 1 s.
  3. Modo: 300 s quieto → ESTACIONADO; 10 s de movimiento → EN_MARCHA; 9 s → sigue ESTACIONADO.
  4. Piso de ruido: se adapta a un ruido de fondo nuevo y no aprende un estallido.
  Imprimir PASA/FALLA por caso y salir con código ≠ 0 si algo falla.

### 6.2 Servidor
- `PuenteMQTT` con mensajes simulados (sin broker): telemetría, gps, evento (y duplicado), LWT, sync.
- Reglas de acción por `nivel_riesgo` (alerta / silenciosa / registro).
- PERSONA no se genera en EN_MARCHA.
- Actualizar `simular_nodo` para que publique por **MQTT** con el contrato de la sección 2 (incluye recorrido GPS simulado por Arequipa a 5 Hz y los cinco tipos de evento).
- `python manage.py test` completo.

---

## 7. Criterios de aceptación
- [ ] Ambos sketches compilan (o, si no hay arduino-cli, las pruebas de host pasan y se indica qué falta verificar en el IDE).
- [ ] El nodo publica telemetría (1 s), GPS (5 Hz en marcha) y eventos QoS 1; al desconectarlo, el dashboard lo marca fuera de línea en ≤ keepalive×1.5 (LWT).
- [ ] El Monitor Serie del nodo muestra modo, piso de ruido, tiempo de FFT y `rtt_sync`.
- [ ] Armar/desarmar desde la web o el bot llega al nodo por `config` retenido.
- [ ] El mapa del panel muestra el vehículo moviéndose en tiempo real con rumbo y rastro.
- [ ] El video del panel llega por el stream MJPEG con YOLO; PERSONA solo alerta estacionado.
- [ ] CHOQUE y ROTURA_VENTANA generan alerta con foto; GOLPE_MANIPULACION alerta silenciosa; IMPACTO_SIN_CONFIRMAR y VIDRIO_ROTO_EXTERNO solo registro.
- [ ] `MedicionTiempo` sigue llenándose (t_deteccion_sensor del nodo sincronizado por MQTT).
- [ ] `python manage.py test` y las pruebas de host pasan.

---

## 8. Documentación — `docs/GUIA_MQTT.md`
Incluir: instalación de Mosquitto en Windows, creación de usuarios y ACL, firewall (1883 y 8000), prueba con `mosquitto_sub -h 127.0.0.1 -u servidor -P <clave> -t "ucsm/v1/#" -v`, librerías del firmware (TinyGPSPlus, ArduinoJson 7, esp-dsp o arduinoFFT, PubSubClient si aplica), datos a editar en `cfg` de cada sketch, conexión de la señal de contacto (esquema con PC817), configuración de la clave de Google Maps, tabla de tópicos y tabla de las reglas de detección con sus umbrales (lista para la tesis).

---

## 9. Fases
1. **Infraestructura:** Mosquitto (conf, usuarios, ACL, .bat), `MqttConfig`, `paho-mqtt`.
2. **Reglas puras + pruebas de host** (`reglas_deteccion.h`, `prueba_reglas.cpp`) — no avanzar hasta que pasen.
3. **Firmware nodo:** MQTT, reloj por sync, MPU ±16 g 500 Hz, FFT, `MaquinaModo`, reglas, GPS 5 Hz, telemetría nueva.
4. **Firmware cámara:** esp_http_server (80 + 81/stream), fps_max, MQTT estado/cmd.
5. **Servidor:** `PuenteMQTT`, `PublicadorConfiguracion`, catálogo y reglas de acción, `ReceptorStreamCamara`, cambios en `VigilanciaVisual` e `IncidenciaService`, `simular_nodo` por MQTT.
6. **Frontend:** mapa en vivo con Google Maps (tarjeta del panel + página), video por stream, estado de modo.
7. **Cierre:** pruebas, `docs/GUIA_MQTT.md`, resumen de cambios y criterios de aceptación.
