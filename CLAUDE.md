# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project overview

Sistema de Monitoreo Vehicular Inteligente (YOLO + IoT) for UCSM — a thesis project. A Django 6
backend receives telemetry/events from an ESP32-S3 node (GPS, MPU6050, INMP441) over a fixed
JSON API, pulls a photo from a paired ESP32-CAM, runs YOLO person detection, stores the incident
(with image) in SQLite, alerts the vehicle owner via Telegram, and records per-stage timing
(network, inference, DB, Telegram) for the thesis's Chapter V results. A Django Channels panel
shows everything live. Full spec: `PROMPT_CLAUDE_CODE_Sistema_Monitoreo_UCSM.md` (treat sections 6
"Base de datos" and 7 "Contrato de la API" as **fixed** — never add/remove/rename fields, tables,
routes, or JSON keys described there; the ESP32 firmware in `firmware/` is already written against
that exact contract).

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

- **Start the real system** (workers + Channels/Daphne + YOLO warm-up): `python main.py`
  - `--sin-vision` disables continuous YOLO on the camera feed
  - `--simular` uses a simulated camera (`media/simulacion/*.jpg`) and disables real Telegram sends — use this for all local testing without hardware
  - `--solo-migrar` just runs migrate + seed and exits
- **Emulate the ESP32-S3** against a running server: `python manage.py simular_nodo --codigo NODO-01 --url http://127.0.0.1:8000 --segundos 60 --eventos 3`
- Seed catalogs + demo data (conductor/vehículo/dispositivo NODO-01 + superuser `admin`/`admin123`): `python manage.py sembrar_catalogos --demo`
- Export the thesis Excel workbook from the CLI: `python manage.py exportar_resultados --desde AAAA-MM-DD --hasta AAAA-MM-DD`
- `python manage.py check` / `python manage.py check --deploy`
- `python manage.py test monitoreo` — full suite (64 tests as of this writing: models, API, pipeline with test doubles, metrics, Channels consumer, web CRUD/login, Telegram bot commands/permissions/invitations/shared-validation/worker, DifusorAlertas)
- `python manage.py migrate`, `python manage.py makemigrations monitoreo`
- `python manage.py collectstatic --noinput` (only matters when `DJANGO_DEBUG=False`; dev serves static files directly)

**Never use `manage.py runserver` for anything beyond quick template/static debugging** — it does not start the background workers (`ProcesadorEventos`, `VigilanciaVisual`, `MonitorDispositivos`) or Channels/Daphne. `main.py` is the only real entry point, and intentionally never starts workers from `AppConfig.ready()` so that `manage.py`/tests stay side-effect free.

## Architecture (MVC + services, all OOP)

- `config.py` — single source of truth for all configuration (frozen dataclasses per concern, loaded from `.env` via `python-dotenv`). `settings.py` and every service/worker import `config` from here only; nothing reads `os.environ` elsewhere. `config.resumen_seguro()` powers the masked view on the Configuración page.
- `main.py` — `ServidorPrincipal`: the only startup path. Order: verify folders → migrate+seed → (SQLite WAL + busy_timeout are actually enabled earlier, see `apps.py` below) → load+warm up YOLO → start workers (incl. the Telegram bot) → print LAN banner → run Daphne programmatically → clean shutdown on Ctrl+C.
- `monitoreo/apps.py` — `MonitoreoConfig.ready()` registers the SQLite `PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;` on every connection (not just under `main.py`) so that `manage.py test`/`shell` also tolerate concurrent writers — needed because `DifusorAlertas` and the bot's update pool write from real threads.
- `sv_ucsm/` — Django project package (`settings.py`, `urls.py`, `asgi.py` wires Channels' `ProtocolTypeRouter`).
- `monitoreo/` — the one app, structured as:
  - `models/` (M, **fixed schema** except `AccesoTelegram` which was added later — see below — one file per aggregate) — `Conductor`, `Vehiculo`, `Dispositivo`/`EstadoDispositivo`, `TipoEvento`, `Incidencia`/`DeteccionYolo`, `MedicionTiempo` (computes all latency fields in `save()`), `AlertaTelegram` (one row **per recipient** of an alert — `conductor` is always the vehicle owner, never the actual recipient; use `AlertaTelegram.destinatario_nombre` to resolve who actually got it), `AccesoTelegram` (who — owner or family — is authorized to use the bot for a given vehicle).
  - `repositories/` — reusable ORM queries (`IncidenciaRepository`, `DispositivoRepository`, `MetricasRepository`).
  - `services/` — the real business logic, each a class: `RelojServidor`, `EstadoEnVivo` (thread-safe in-memory live state), `NotificadorTiempoReal` (Channels group_send), `TelemetriaService`, `camara_service.py` (`ClienteCamaraESP32`/`CamaraSimulada`/`FabricaCamara`), `DetectorYOLO` (singleton, lock, warm-up, tolerates missing weights), `IncidenciaService` (the full pipeline — see below), `DifusorAlertas` (broadcasts one incident's alert to every authorized+subscribed recipient in parallel — see below), `TelegramService` (now just single-recipient ops: `enviar_prueba`/`reintentar`), `MapasService`, `MetricasService` (now also `entrega_alertas`/`resumen_entrega_alertas`), `DisponibilidadService`, `ExportacionService`.
  - `bot/` — the multi-user Telegram bot (owner + family), long-polling only (no webhook — the server has no public IP): `cliente_bot_api.py` (`ClienteBotAPI`, the only thing that talks HTTP to the Bot API; `TelegramService` and `DifusorAlertas` share it too), `contexto.py` (`ContextoComando`, a plain data holder passed to every command), `autorizacion.py` (`ServicioAccesos` — chat_id → visible vehicles/permissions; `TELEGRAM_ADMIN_CHAT_IDS` chats bypass all per-vehicle checks), `invitaciones.py` (`ServicioInvitaciones` — 6-char codes excluding `0/O/1/I`, expire after `TELEGRAM_INVITACION_HORAS`), `limitador.py` (`LimitadorFrecuencia`, in-memory singleton: per-chat rate limit + failed-linking lockout), `teclados.py`/`formateador.py` (inline/reply keyboards and HTML message bodies), `estado_bot.py` (`EstadoBot`, in-memory singleton the Configuración page reads), `registro_metricas.py` (`RegistroComandosBot` → appends to `logs/bot_comandos.csv`), `metricas_bot_service.py` (reads that CSV back for the Resultados page), `comandos/` (one `ComandoBase` subclass per command — `base.py`'s `resolver_vehiculo_o_responder`/`resolver_dispositivo_o_responder` handle the placa-arg / single-vehicle / multi-vehicle-picker logic shared by almost every command), `manejador_callbacks.py` (`ManejadorCallbacks` — routes inline-button `callback_data` by short prefix: `v`/`f`/`u`/`d`/`ok`/`rv`/`pc`/`sel`).
  - `workers/` — background threads started only by `main.py`: `WorkerBase` (start/stop/loop + per-cycle exception isolation so a thread never dies), `ProcesadorEventos` (owns the event queue + dedup cache; API just enqueues), `VigilanciaVisual` (continuous YOLO over the live camera feed, confirms N frames before raising a PERSONA incident), `MonitorDispositivos` (offline detection, camera status polling), `BotTelegramWorker` (one thread does `getUpdates` long-polling; each update is handed to a 3-thread pool so one slow `/foto` doesn't block other users; gracefully no-ops if `TELEGRAM_BOT_TOKEN` is empty or `getMe` fails — never crashes `main.py`).
  - `controllers/web/` and `controllers/api/` — class-based views (C). Web pages require login (`LoginRequiredMixin`); `/api/v1/telemetria/` and `/api/v1/eventos/` use a custom `AutenticacionApiKey` (DRF) instead of sessions and are CSRF-exempt by construction (no `SessionAuthentication`), everything else keeps CSRF on. `acceso_telegram_controller.py` handles the "invite family member" web flow (generates the code + a `qrcode`-rendered PNG, revoke, toggle control permission).
  - `security/middleware.py` — `MarcaTiempoRecepcionMiddleware` is first in `MIDDLEWARE`; it stamps `request.t_recepcion_ms` (used as both `t_recepcion_servidor` and the `server_ts_ms` echoed back to the firmware for clock sync) and enforces the 64KB body cap on the two firmware routes.
  - `consumers/panel_consumer.py` + `routing.py` — `PanelConsumer` (rejects anonymous sockets, joins group `"panel"`); `NotificadorTiempoReal` is the only thing that sends into that group from sync code.
  - `templates/monitoreo/` (V) + `static/monitoreo/{css,js,img}` — `app.css` is the whole design system (two-tone green `#0cb967`/white only, see spec §9.1), `js/ws.js` (`ConexionTiempoReal`: WS with exponential backoff + sondeo fallback to `GET /api/v1/panel/estado/`), `js/panel.js`/`mapa.js` consume it. The vehicle detail page has an "Acceso por Telegram" card (list accesses, invite-with-QR, revoke, toggle control); the Configuración page shows live bot status (`EstadoBot`); Resultados has "Consultas por Telegram" and "Entrega de alertas" sections.
  - `management/commands/` — `sembrar_catalogos`, `simular_nodo` (firmware emulator), `exportar_resultados`. (There is intentionally no `telegram_chat_ids` command anymore — linking is exclusively through the bot's `/vincular` and `/unirse`/`/start CODIGO` now.)
- `static/vendor/` — Bootstrap 5.3, Bootstrap Icons, Chart.js 4, Leaflet 1.9, and self-hosted Montserrat/Open Sans `.woff2` files, all downloaded once (no CDN at runtime, so the LAN-only deployment works without internet).
- `firmware/` — the two `.ino` sketches (ESP32-S3 node, ESP32-CAM). Treat their JSON/header contract as the ground truth when touching the API layer.

### The incidence pipeline (`IncidenciaService`)

`procesar_evento_sensor(evento)` (called by `ProcesadorEventos`'s worker thread) and
`registrar_incidencia_persona(...)` (called by `VigilanciaVisual` when YOLO confirms a person over
N consecutive frames) both funnel into a shared `_persistir_y_notificar(...)`: save
`Incidencia`+`DeteccionYolo` in one transaction → emit `incidencia_nueva` over the socket →
`DifusorAlertas.difundir(incidencia, resultado_yolo)` (unless `TELEGRAM_ALERTAR_SIN_PERSONA=False`
and no person was found) → save `MedicionTiempo` (`t_confirmacion_telegram` = the *first* recipient's
confirmation, not an arbitrary single send) → emit `incidencia_actualizada` → optional reverse
geocoding last (never before the alert, so it can't add latency). All camera/YOLO/`DifusorAlertas`
dependencies are constructor-injected (the param is named `difusor_alertas`, not `telegram_service`),
which is how `monitoreo/tests/test_pipeline.py` swaps in a fake without touching real
hardware/network.

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
`media/simulacion/*.jpg` (auto-generated placeholders via Pillow if that folder is empty). Combine
with `manage.py simular_nodo` to emulate the ESP32-S3's HTTP traffic end to end, including its
clock-sync handshake (`server_ts_ms`). For the bot, `monitoreo/tests/dobles_bot.py`'s
`ClienteBotAPIFalso` is the shared fake — every bot/`DifusorAlertas` test injects it instead of the
real `ClienteBotAPI` so the suite never makes a real network call. One gotcha: tests that exercise
`DifusorAlertas` or `BotTelegramWorker.ciclo()` for real (not via a fully-faked dependency) spawn
actual OS threads that touch the DB — those test classes must use `TransactionTestCase`, not
`TestCase`, or Django's per-test atomic transaction deadlocks against the worker threads' own
connections (see `test_difusor_alertas.py`).
