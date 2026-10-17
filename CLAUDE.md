# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Sistema de Monitoreo Vehicular Inteligente (YOLO + IoT) for UCSM — a thesis project. A Django 6
backend receives telemetry/events from an ESP32-S3 node (GPS, MPU6050, INMP441) **over MQTT**,
takes a frame from the paired ESP32-CAM's MJPEG stream, runs YOLO person detection, stores the
incident (with image) in SQLite, alerts the vehicle owner via Telegram, and records per-stage
timing (network, inference, DB, Telegram) for the thesis's Chapter V results. A Django Channels
panel shows everything live, including a real-time map. Two specs, both authoritative:

- `PROMPT_CLAUDE_CODE_Sistema_Monitoreo_UCSM.md` — the original system. Sections 6 "Base de datos"
  and 7 "Contrato de la API" are **fixed**: never remove/rename fields, tables, routes, or JSON
  keys described there. The HTTP endpoints still exist as a fallback.
- `PROMPT_FIRMWARE_MQTT_REGLAS_GPS.md` — the **v3.0.0 migration (implemented)**: MQTT transport,
  the definitive detection rules, GPS at 5 Hz, the MJPEG stream and the live map. Its section 2
  (MQTT topic contract) is **fixed** the same way; the firmware in `firmware/` is written against
  that exact contract. Start here for anything touching transport, rules, or the map.

Operator-facing guide for the MQTT side (Mosquitto install, users/ACL, firewall, the rules table,
the Google Maps key, troubleshooting): `docs/GUIA_MQTT.md`.

Everything (code, comments, class/method names, UI text) is in Spanish, except reserved words and
library names. All business logic lives in classes (services/workers/clients) — never loose logic
in view functions.

## Environment

```
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env    # first time only; edit secrets as needed
```

## Common commands

Run from the repository root:

- **Start the MQTT broker first** (separate console, leave it open): `infra\mosquitto\iniciar_mosquitto.bat`
  (run it as Administrator the first time — it creates `infra/mosquitto/passwd` and the firewall rule)
- **Start the real system** (workers + Channels/Daphne + YOLO warm-up): `python main.py`
  - `--sin-vision` disables continuous YOLO on the camera feed
  - `--simular` uses a simulated camera (`media/simulacion/*.jpg`) and disables real Telegram sends — use this for all local testing without hardware
  - `--sin-mqtt` skips `PuenteMQTT` entirely (only the HTTP fallback) — use when Mosquitto isn't installed
  - `--solo-migrar` just runs migrate + seed and exits
- **Emulate the ESP32-S3 v3.0.0 over MQTT**: `python manage.py simular_nodo --codigo NODO-01 --segundos 120 --eventos 5`
  (drives a GPS route through Arequipa at 5 Hz and fires all five rule events with full evidence;
  `--duplicar-eventos` resends each event with the **same** `evento_uid` to exercise QoS-1 dedup;
  `--http` uses the HTTP fallback instead of MQTT)
- **Host tests of the firmware rules** (no ESP32 needed, 47 cases): `firmware\pruebas\compilar_pruebas.bat`
  (needs g++ or MSVC on PATH; `C:\Strawberry\c\bin` has a working MinGW g++ on this machine)
- Seed catalogs + demo data (conductor/vehículo/dispositivo NODO-01 + superuser `admin`/`admin123`): `python manage.py sembrar_catalogos --demo`
- Export the thesis Excel workbook from the CLI: `python manage.py exportar_resultados --desde AAAA-MM-DD --hasta AAAA-MM-DD`
- `python manage.py check` / `python manage.py check --deploy`
- `python manage.py test monitoreo` — full suite (161 tests as of this writing: models, API, pipeline with test doubles, metrics, Channels consumer, web CRUD/login, Telegram bot commands/permissions/invitations/shared-validation/worker, DifusorAlertas, **`PuenteMQTT` with simulated messages, the `nivel_riesgo` action policy, PERSONA-only-when-parked, the frame buffer/provider, the MJPEG frame splitter, and the custom model's five classes end-to-end (`test_deteccion_visual.py`)**)
- `python manage.py migrate`, `python manage.py makemigrations monitoreo`
- `python manage.py collectstatic --noinput` (only matters when `DJANGO_DEBUG=False`; dev serves static files directly)

**Never use `manage.py runserver` for anything beyond quick template/static debugging** — it does not start the background workers (`PuenteMQTT`, `ReceptorStreamCamara`, `ProcesadorEventos`, `VigilanciaVisual`, `MonitorDispositivos`) or Channels/Daphne. `main.py` is the only real entry point, and intentionally never starts workers from `AppConfig.ready()` so that `manage.py`/tests stay side-effect free. Consequence to remember: under `runserver` or in tests, `PuenteMQTT.activa()` and `ReceptorStreamCamara._activa` are `None`, so the Configuración page shows neutral values and `ProveedorCuadros` falls back to `/capture` — that's by design, not a bug.

## Architecture (MVC + services, all OOP)

- `config.py` — single source of truth for all configuration (frozen dataclasses per concern, loaded from `.env` via `python-dotenv`). `settings.py` and every service/worker import `config` from here only; nothing reads `os.environ` elsewhere. `config.resumen_seguro()` powers the masked view on the Configuración page. v3.0.0 added `MqttConfig` (with the `topico`/`comodin`/`codigo_de_topico`/`sufijo_de_topico` topic helpers — **always build topics through these**, never by string concatenation), `UmbralesDeteccionConfig` (the 14 rule thresholds published in `config`), `StreamCamaraConfig`, and `Esp32CamConfig.urls_stream_por_dispositivo`/`fps_max`. `CATALOGO_CLASES_YOLO` + `YoloConfig.clases_vigiladas` map each class of the thesis's own model to a `TipoEvento` code, its own alert confidence and whether it only alerts when parked — **this mapping is what makes a detection reach Telegram**, so a new class has to be added there (see "Which YOLO classes raise an incident" below).
- `main.py` — `ServidorPrincipal`: the only startup path. Order: verify folders → migrate+seed → (SQLite WAL + busy_timeout are actually enabled earlier, see `apps.py` below) → load+warm up YOLO → start workers (`PuenteMQTT` **first**, so the server is already subscribed and has published retained `config` before the node connects) → print LAN banner → run Daphne programmatically → clean shutdown on Ctrl+C.
- `infra/mosquitto/` — broker config, ACL, and `iniciar_mosquitto.bat`. `passwd` is generated by the `.bat` and gitignored.
- `monitoreo/apps.py` — `MonitoreoConfig.ready()` registers the SQLite `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` on every connection (not just under `main.py`) so that `manage.py test`/`shell` also tolerate concurrent writers — needed because `DifusorAlertas` and the bot's update pool write from real threads.
- `sv_ucsm/` — Django project package (`settings.py`, `urls.py`, `asgi.py` wires Channels' `ProtocolTypeRouter`).
- `monitoreo/` — the one app, structured as:
  - `models/` (M, **fixed schema** except `AccesoTelegram` which was added later — see below — one file per aggregate) — `Conductor`, `Vehiculo`, `Dispositivo`/`EstadoDispositivo`, `TipoEvento`, `Incidencia`/`DeteccionYolo`, `MedicionTiempo` (computes all latency fields in `save()`), `AlertaTelegram` (one row **per recipient** of an alert — `conductor` is always the vehicle owner, never the actual recipient; use `AlertaTelegram.destinatario_nombre` to resolve who actually got it), `AccesoTelegram` (who — owner or family — is authorized to use the bot for a given vehicle).
  - `repositories/` — reusable ORM queries (`IncidenciaRepository`, `DispositivoRepository`, `MetricasRepository`).
  - `services/` — the real business logic, each a class: `RelojServidor`, `EstadoEnVivo` (thread-safe in-memory live state; v3.0.0 also holds the **GPS trail** of the last 300 points, the vehicle `modo`, and the MQTT online/offline flag), `NotificadorTiempoReal` (Channels group_send; added `gps`/`modo`/`camara_estado`), `TelemetriaService`, `camara_service.py` (`ClienteCamaraESP32`/`CamaraSimulada`/`FabricaCamara` **plus `ProveedorCuadros`**, which prefers the stream buffer over `/capture`), `buffer_cuadros.py` (`BufferCuadros`/`RegistroBuffers`: the 3-second circular frame buffer), `mqtt_service.py` (`ClienteMqtt` — the only thing that talks MQTT — and `PublicadorConfiguracion`, which publishes retained `config` and the `cmd`/`cam/cmd` commands), `reglas_accion.py` (`ReglasAccion`/`DescriptorEvidencia`: the `nivel_riesgo` → action policy and the human-readable evidence text), `control_armado_service.py` (`ControlArmadoService`: the **single** path for arming/disarming, used by web, API and bot), `DetectorYOLO` (singleton, lock, warm-up, tolerates missing weights), `IncidenciaService` (the full pipeline — see below), `DifusorAlertas` (broadcasts one incident's alert to every authorized+subscribed recipient in parallel — see below), `TelegramService` (now just single-recipient ops: `enviar_prueba`/`reintentar`), `MapasService`, `MetricasService` (now also `entrega_alertas`/`resumen_entrega_alertas`), `DisponibilidadService`, `ExportacionService`.
  - `bot/` — the multi-user Telegram bot (owner + family), long-polling only (no webhook — the server has no public IP): `cliente_bot_api.py` (`ClienteBotAPI`, the only thing that talks HTTP to the Bot API; `TelegramService` and `DifusorAlertas` share it too), `contexto.py` (`ContextoComando`, a plain data holder passed to every command), `autorizacion.py` (`ServicioAccesos` — chat_id → visible vehicles/permissions; `TELEGRAM_ADMIN_CHAT_IDS` chats bypass all per-vehicle checks), `invitaciones.py` (`ServicioInvitaciones` — 6-char codes excluding `0/O/1/I`, expire after `TELEGRAM_INVITACION_HORAS`), `limitador.py` (`LimitadorFrecuencia`, in-memory singleton: per-chat rate limit + failed-linking lockout), `teclados.py`/`formateador.py` (inline/reply keyboards and HTML message bodies), `estado_bot.py` (`EstadoBot`, in-memory singleton the Configuración page reads), `registro_metricas.py` (`RegistroComandosBot` → appends to `logs/bot_comandos.csv`), `metricas_bot_service.py` (reads that CSV back for the Resultados page), `comandos/` (one `ComandoBase` subclass per command — `base.py`'s `resolver_vehiculo_o_responder`/`resolver_dispositivo_o_responder` handle the placa-arg / single-vehicle / multi-vehicle-picker logic shared by almost every command), `manejador_callbacks.py` (`ManejadorCallbacks` — routes inline-button `callback_data` by short prefix: `v`/`f`/`u`/`d`/`ok`/`rv`/`pc`/`sel`).
  - `workers/` — background threads started only by `main.py`: `WorkerBase` (start/stop/loop + per-cycle exception isolation so a thread never dies), **`PuenteMQTT`** (subscribes to `ucsm/v1/+/{telemetria,gps,evento,estado,modo,sync/req,cam/estado}` and dispatches each topic to the existing services — see below), **`ReceptorStreamCamara`** (one thread per camera keeps `GET /stream` open, splits JPEGs by SOI/EOI markers and fills `BufferCuadros`), `ProcesadorEventos` (owns the event queue + dedup cache; both MQTT and the HTTP API just enqueue), `VigilanciaVisual` (continuous YOLO over the stream buffer; keeps a **separate confirmation streak and cooldown per YOLO class**, and the "only when ESTACIONADO and armed" restriction is **per class** — `person` keeps it, weapons/damage alert in any mode; takes an injectable `incidencia_service` so tests never build a real `DifusorAlertas`), `MonitorDispositivos` (now only a **fallback**: the MQTT Last Will decides online/offline, and this worker steps aside when `EstadoEnVivo` has a recent MQTT state), `BotTelegramWorker` (one thread does `getUpdates` long-polling; each update is handed to a 3-thread pool so one slow `/foto` doesn't block other users; gracefully no-ops if `TELEGRAM_BOT_TOKEN` is empty or `getMe` fails — never crashes `main.py`).
  - `controllers/web/` and `controllers/api/` — class-based views (C). Web pages require login (`LoginRequiredMixin`); `/api/v1/telemetria/` and `/api/v1/eventos/` use a custom `AutenticacionApiKey` (DRF) instead of sessions and are CSRF-exempt by construction (no `SessionAuthentication`), everything else keeps CSRF on. `acceso_telegram_controller.py` handles the "invite family member" web flow (generates the code + a `qrcode`-rendered PNG, revoke, toggle control permission).
  - `security/middleware.py` — `MarcaTiempoRecepcionMiddleware` is first in `MIDDLEWARE`; it stamps `request.t_recepcion_ms` (used as both `t_recepcion_servidor` and the `server_ts_ms` echoed back to the firmware for clock sync) and enforces the 64KB body cap on the two firmware routes.
  - `consumers/panel_consumer.py` + `routing.py` — `PanelConsumer` (rejects anonymous sockets, joins group `"panel"`); `NotificadorTiempoReal` is the only thing that sends into that group from sync code.
  - `templates/monitoreo/` (V) + `static/monitoreo/{css,js,img}` — `app.css` is the whole design system (two-tone green `#0cb967`/white only, see spec §9.1), `js/ws.js` (`ConexionTiempoReal`: WS with exponential backoff + sondeo fallback to `GET /api/v1/panel/estado/`), `js/panel.js`/`mapa.js`/**`mapa_vivo.js`** consume it. `mapa_vivo.js` holds the live map: a `CapaMapa` abstraction with `CapaGoogleMaps` and `CapaLeaflet` implementations, and `MapaVehiculoVivo` on top (heading-rotated arrow, ~200 ms interpolation between points so 5 Hz doesn't look like jumps, 5-minute trail, accuracy circle ≈ `hdop × 2.5 m`, follow toggle, "Sin señal GPS" after 5 s). The card markup lives in `componentes/_mapa_vivo.html` and is shared by the panel and the full-page "Mapa en vivo" — all element ids are built from a `prefijo`, so the same component can appear twice without colliding. The vehicle detail page has an "Acceso por Telegram" card (list accesses, invite-with-QR, revoke, toggle control); the Configuración page shows live bot status (`EstadoBot`), MQTT/stream health, and buttons that publish `cmd`/`cam/cmd`; Resultados has "Consultas por Telegram" and "Entrega de alertas" sections.
  - `management/commands/` — `sembrar_catalogos` (the catalog now carries the five v3.0.0 rule types; `nivel_riesgo` there **is** the action policy, so changing it changes behaviour), `simular_nodo` (MQTT firmware emulator, `--http` for the fallback), `exportar_resultados`. (There is intentionally no `telegram_chat_ids` command anymore — linking is exclusively through the bot's `/vincular` and `/unirse`/`/start CODIGO` now.)
- `static/vendor/` — Bootstrap 5.3, Bootstrap Icons, Chart.js 4, Leaflet 1.9, and self-hosted Montserrat/Open Sans `.woff2` files, all downloaded once (no CDN at runtime, so the LAN-only deployment works without internet).
- `firmware/` — the two `.ino` sketches, both **v3.0.0** and MQTT-based. Treat their JSON/topic contract as the ground truth when touching the transport layer.
  - `nodo_iot_esp32s3/nodo_iot_esp32s3.ino` — `ClienteMQTT` (ESP-IDF `esp-mqtt`, own task on core 0, two FreeRTOS queues: events at QoS 1 with priority, telemetry/GPS at QoS 0 dropping the oldest), `RelojSincronizado` (sync by `sync/req`→`sync/resp`, best-RTT sample in a 60 s window), `OrquestadorReglas`, `SensorIMU` (500 Hz, ±16 g), `SensorAudio` (16 kHz, 256-sample blocks, FFT + `fft_us` timing), `SensorGPS` (UBX config to 5 Hz / 38400 baud with verification and fallback to 9600), `NodoVehicular`.
  - `nodo_iot_esp32s3/reglas_deteccion.h` — **the one allowed exception to "one file per sketch"**: the pure rule logic (`UmbralesDeteccion`, `AnalizadorEspectral`, `EstimadorPiso`, `ReglaChoque`, `ReglaRotura`, `MaquinaModo`) with **zero** Arduino/FreeRTOS/hardware dependencies, so it compiles and is tested on the PC. Keep it that way: if you need `Arduino.h` in here, the design is wrong — put the hardware part in the `.ino`.
  - `esp32cam_camara/esp32cam_camara.ino` — `esp_http_server` on port 80 (`/capture`, `/status`, `/config`) **and a second server on port 81** (`/stream`, MJPEG, one client), `fps_max`, `ClienteMQTTCamara` (retained `cam/estado` + Last Will, obeys `cam/cmd`).
  - `firmware/pruebas/` — `prueba_reglas.cpp` + `compilar_pruebas.bat`: 47 host test cases over synthetic 16 kHz signals (crash, window break, vehicle mode, noise floor, spectral analyzer). **Run these after any change to `reglas_deteccion.h`** — they're the only way to validate the rules without hardware.

### Concurrency inside the node firmware

Three contexts touch the rules: the IMU task (500 Hz, core 1, prio 4), the audio task (every 16 ms,
core 1, prio 3) and `loop()`. `OrquestadorReglas` is the single owner and guards every entry point
with a very short `portENTER_CRITICAL` section — the rules only do comparisons, while the expensive
work (FFT, JSON, MQTT publish) always stays **outside** the lock. Two rules to keep: never call a
locking method from inside another lock (that deadlocks), and never enqueue from inside the lock.
Decisions are pushed to a FreeRTOS queue and `loop()` serializes them into JSON.

### The MQTT bridge (`PuenteMQTT`)

Everything the node and the camera publish enters here; the HTTP endpoints are a fallback sharing
the same services and the same serializers. Three details that are load-bearing and easy to break:

- `t_recepcion_ms` is taken in `ClienteMqtt._on_message` **before** parsing the JSON, so
  `medicion_tiempo.t_recepcion_servidor` measures the network and not the server's own work.
- `sync/req` is answered **immediately and without touching the DB** — any query would inflate the
  RTT the node uses to compute its clock offset. Don't add logging/DB calls to that handler.
- paho's callbacks run on **its own thread**, so every handler calls `close_old_connections()` when
  it finishes (`procesar_mensaje` does it centrally, skipping it for `sync/req`).

`PuenteMQTT.__init__` registers its client with the `PublicadorConfiguracion` singleton (not
`iniciar()`), so the web/bot can publish retained `config` as soon as the bridge object exists.
Tests inject a `ClienteMqttFalso` and call the handlers directly — no broker needed. Because both
`EstadoEnVivo` and `PublicadorConfiguracion` are process singletons, those tests reset
`EstadoEnVivo._instancia` **before** constructing the bridge (the bridge captures the instance it
sees) and clear `PublicadorConfiguracion._instancia` in `tearDown`.

### The incidence pipeline (`IncidenciaService`)

`procesar_evento_sensor(evento)` (called by `ProcesadorEventos`'s worker thread) and
`registrar_incidencia_visual(..., tipo_codigo, clase_yolo)` (called by `VigilanciaVisual` when YOLO
confirms a watched class over N consecutive frames; `registrar_incidencia_persona` is a thin alias
for the `person` case) both funnel into a shared `_persistir_y_notificar(...)`: pick the stream frame
**closest to `t_deteccion_sensor`** (falling back to `/capture`, recording which was used in
`observacion`) → save `Incidencia`+`DeteccionYolo` in one transaction → emit `incidencia_nueva` over
the socket → alert *only if* `ReglasAccion.para(tipo)` says so → save `MedicionTiempo`
(`t_confirmacion_telegram` = the *first* recipient's confirmation, not an arbitrary single send) →
emit `incidencia_actualizada` → optional reverse geocoding last (never before the alert, so it can't
add latency). All camera/YOLO/`DifusorAlertas` dependencies are constructor-injected (the param is
named `difusor_alertas`, not `telegram_service`), which is how `monitoreo/tests/test_pipeline.py`
swaps in a fake without touching real hardware/network.

### Who decides what happens to an incident (`ReglasAccion`)

The **database catalog** decides, not the firmware. `tipo_evento.nivel_riesgo` maps to: `ALTO` →
alert with notification, `MEDIO` → **silent** alert (`disable_notification=True`), `BAJO` → incident
only, no Telegram. The `nivel` the node proposes is validated against the catalog and the result is
written into `observacion`; a mismatch means firmware and catalog drifted apart. The practical
upside: the thesis can change the policy from the "Tipos de evento" page without reflashing the
ESP32 — there's a test for exactly that (`test_cambiar_el_nivel_del_catalogo_cambia_la_accion`).

One deliberate exception in `_tipo_final`: a `CHOQUE` is **not** reclassified to what YOLO sees,
because it's an emergency in its own right and reclassifying would lose its evidence; every other
sensor type is reclassified to the `TipoEvento` of the highest-confidence watched detection.

### Which YOLO classes raise an incident (`config.yolo.clases_vigiladas`)

The thesis's own weights (`modelos_ia/yolo26_ucsm_best.pt`) have **no `person` class** — they have
`abriendo_puerta`, `arma_blanca`, `arma_fuego`, `car broken` (with a space) and `suspicious`. Nothing
in the detection path may compare a class name against `"person"` by hand: every box is resolved
through `CATALOGO_CLASES_YOLO` in `config.py`, which gives it a `TipoEvento` code
(`APERTURA_PUERTA`, `ARMA_BLANCA`, `ARMA_FUEGO`, `VEHICULO_DANADO`, `PERSONA_SOSPECHOSA`, `PERSONA`),
its own alert confidence, and whether it only alerts when parked+armed. A class that is not in that
catalog is drawn on the frame but **can never produce an incident or an alert** — that was the
original bug: `hay_persona` hardcoded `"person"`, so with these weights it was always `False` and
nothing ever reached Telegram.

Consequences worth keeping in mind:

- `sembrar_catalogos` derives the vision `TipoEvento` rows from that same catalog, so the DB and the
  detector cannot drift. `IncidenciaService._obtener_tipo` also self-seeds a missing type (with a
  warning) rather than raising `DoesNotExist` inside the worker thread and losing the alert.
- `ResultadoDeteccion.hay_persona` keeps its **literal** meaning (class `person` only), because
  `PERSONA` has its own parked+armed rule. For "is there anything to alert?" use `hay_alerta`.
  `num_personas` is kept as an alias of `num_detecciones` because it's a fixed API/WS key.
- `.env` tuning: `YOLO_CLASES` must match `model.names` **exactly**; `YOLO_CONFIANZA_POR_CLASE`
  overrides a single class's threshold; `YOLO_CLASES_SOLO_ESTACIONADO` lists the classes restricted
  to ESTACIONADO+armed (default: just `person`).
- `DetectorYOLO` logs the model's real class list at startup and warns about classes in `YOLO_CLASES`
  that the model doesn't have, and about model classes with no `TipoEvento` — check that log first
  when "it detects but doesn't alert".

### `DifusorAlertas` — who actually receives an alert

Recipients = every active `AccesoTelegram` for that vehicle with `recibe_alertas=True` and not
currently `silenciado_hasta` in the future, plus `TELEGRAM_ADMIN_CHAT_IDS`; if that list ends up
empty, it falls back to `Conductor.telegram_chat_id` (back-compat path). One `AlertaTelegram` row is
created **per recipient** and sent concurrently via a `ThreadPoolExecutor` — the slow part (the
Telegram HTTP call) is what actually runs in parallel; the SQLite writes themselves are still
serialized behind `difusor_alertas._LOCK_ESCRITURA_BD` because SQLite is single-writer and
concurrent inserts from separate threads/connections raise `OperationalError: database table is
locked` otherwise (this bit me once — don't remove that lock to "simplify" the code). Validating an
alert (`✅ Es real` / `❌ Falsa alarma`, handled in `ManejadorCallbacks._validar`) edits the inline
keyboard on *every* chat that received that incident's alert (via each `AlertaTelegram.message_id`),
and the first click wins — late clicks get an `answerCallbackQuery` telling them who already validated.

### Testing without hardware / without a real Telegram bot

`ESP32CAM_SIMULADA=True` (or `main.py --simular`) swaps in `CamaraSimulada`, which serves
`media/simulacion/*.jpg` (auto-generated placeholders via Pillow if that folder is empty) and makes
`ReceptorStreamCamara` no-op. Combine with `manage.py simular_nodo` to emulate the ESP32-S3 end to
end over MQTT, including its clock-sync handshake, the GPS route at 5 Hz and all five rule events.
`--sin-mqtt` + `simular_nodo --http` covers the HTTP fallback path instead. For the bot, `monitoreo/tests/dobles_bot.py`'s
`ClienteBotAPIFalso` is the shared fake — every bot/`DifusorAlertas` test injects it instead of the
real `ClienteBotAPI` so the suite never makes a real network call. One gotcha: tests that exercise
`DifusorAlertas` or `BotTelegramWorker.ciclo()` for real (not via a fully-faked dependency) spawn
actual OS threads that touch the DB — those test classes must use `TransactionTestCase`, not
`TestCase`, or Django's per-test atomic transaction deadlocks against the worker threads' own
connections (see `test_difusor_alertas.py`).
