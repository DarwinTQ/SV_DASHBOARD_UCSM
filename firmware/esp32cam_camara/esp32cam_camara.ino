/*
 * ============================================================================
 *  CAMARA DEL NODO VEHICULAR - ESP32-CAM (AI-Thinker, OV2640)       v3.0.0
 *  Tesis: Sistema inteligente IoT con YOLO para monitoreo vehicular (UCSM)
 * ============================================================================
 *  La ESP32-CAM NO decide nada: entrega imagen al servidor Django (main.py),
 *  que es el UNICO que se conecta a ella. El navegador y Telegram reciben las
 *  imagenes ya anotadas desde el servidor.
 *
 *  QUE CAMBIA RESPECTO A v2.0.0
 *    - Se reemplaza WebServer por esp_http_server (igual que el ejemplo oficial
 *      CameraWebServer): soporta respuestas multipart y varios clientes sin
 *      bloquear el loop().
 *    - Nuevo SEGUNDO servidor en el puerto 81 con /stream (MJPEG continuo).
 *      El servidor lo lee todo el tiempo y mantiene un buffer circular de 3 s,
 *      asi la incidencia usa el cuadro mas cercano al instante del sensor sin
 *      esperar una captura nueva (menor tiempo de respuesta).
 *    - fps_max configurable (por defecto 8) para no saturar el Wi-Fi.
 *    - MQTT: publica cam/estado retenido al conectar y cada 30 s, con Last Will
 *      {"en_linea":false}, y obedece cam/cmd (resolucion, calidad, fps_max,
 *      flash, reiniciar).
 *
 *  SERVIDOR DE CAPTURA (puerto 80) — requiere X-API-Key: <clave>  o  ?key=<clave>
 *    GET /capture            -> image/jpeg (foto actual)
 *    GET /capture?flash=1    -> igual, encendiendo el LED flash (GPIO4)
 *    GET /status             -> JSON con el estado de la camara
 *    GET /config?resolucion=VGA&calidad=12&fps_max=8
 *                            -> cambia resolucion (QVGA, VGA, SVGA, XGA, HD),
 *                               calidad JPEG (10 = mejor ... 63 = peor) y fps_max
 *
 *  SERVIDOR DE STREAM (puerto 81) — requiere ?key=<clave>
 *    GET /stream             -> multipart/x-mixed-replace; boundary=cuadroUCSM
 *                               UN SOLO cliente a la vez (si entra otro: 503).
 *                               Cada cuadro lleva X-Timestamp-Ms (uptime).
 *
 *  TOPICOS MQTT
 *    PUBLICA  ucsm/v1/<CODIGO_NODO>/cam/estado   QoS 1, RETENIDO (+ Last Will)
 *    ESCUCHA  ucsm/v1/<CODIGO_NODO>/cam/cmd      QoS 1
 *
 *  ARQUITECTURA (POO)
 *    Camara            sensor OV2640: captura, resolucion, calidad, flash
 *    ServidorCaptura   puerto 80 (/capture, /status, /config)
 *    ServidorStream    puerto 81 (/stream MJPEG, un cliente, fps_max)
 *    ClienteMQTTCamara estado retenido + Last Will + comandos
 *    NodoCamara        orquestador
 *
 *  Placa en Arduino IDE: "AI Thinker ESP32-CAM" (core arduino-esp32 3.x)
 *  Librerias: ArduinoJson 7.x
 *  Alimentacion: 5 V / 2 A estable (los reinicios suelen ser por fuente debil).
 * ============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "esp_http_server.h"
#include "esp_timer.h"
#include "mqtt_client.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================================
//  CONFIGURACION (EDITAR AQUI)
// ============================================================================
namespace cfg {
  const char* WIFI_SSID = "Papitaa";
  const char* WIFI_PASS = "person123";

  const char* API_KEY          = "tesis-ucsm-2026";   // = ESP32CAM_API_KEY en .env
  const char* CODIGO_CAMARA    = "CAM-NODO-01";       // informativo
  const char* VERSION_FIRMWARE = "3.0.0";

  // ----- MQTT: la camara comparte el prefijo del NODO al que pertenece -------
  //  Sus topicos son ucsm/v1/<CODIGO_NODO>/cam/estado y .../cam/cmd
  const char* MQTT_HOST        = "10.189.44.103";     // IP de la PC con main.py
  constexpr uint16_t MQTT_PUERTO = 1883;
  const char* MQTT_USUARIO     = "cam01";             // usuario de infra\mosquitto\passwd
  const char* MQTT_CLAVE       = "ucsm2026cam";       // CAMBIAR en produccion
  const char* PREFIJO_TOPICO   = "ucsm/v1";
  const char* CODIGO_NODO      = "NODO-01";           // = dispositivo.codigo del nodo
  constexpr uint16_t MQTT_KEEPALIVE_S = 15;
  constexpr uint32_t ESTADO_PERIODO_MS = 30000;       // cam/estado cada 30 s

  // IP fija recomendada para que config.py siempre apunte a la misma URL.
  // Pon USAR_IP_FIJA = false para usar DHCP (mira la IP en el Monitor Serie).
  constexpr bool USAR_IP_FIJA = true;
  const IPAddress IP_FIJA(10, 189, 44, 120); 
  const IPAddress GATEWAY(10, 189, 44, 1);
  const IPAddress MASCARA(255, 255, 255, 0);
  const IPAddress DNS(8, 8, 8, 8);

  constexpr uint16_t PUERTO_CAPTURA = 80;
  constexpr uint16_t PUERTO_STREAM  = 81;

  constexpr framesize_t RESOLUCION_INICIAL = FRAMESIZE_VGA;   // 640x480 (bueno para YOLO)
  constexpr int CALIDAD_JPEG_INICIAL = 12;
  constexpr int FPS_MAX_INICIAL      = 8;    // = ESP32CAM_FPS_MAX en .env
  constexpr int PIN_FLASH = 4;
}

// ----- Pines AI-Thinker -----
#define PWDN_GPIO_NUM  32
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  0
#define SIOD_GPIO_NUM  26
#define SIOC_GPIO_NUM  27
#define Y9_GPIO_NUM    35
#define Y8_GPIO_NUM    34
#define Y7_GPIO_NUM    39
#define Y6_GPIO_NUM    36
#define Y5_GPIO_NUM    21
#define Y4_GPIO_NUM    19
#define Y3_GPIO_NUM    18
#define Y2_GPIO_NUM    5
#define VSYNC_GPIO_NUM 25
#define HREF_GPIO_NUM  23
#define PCLK_GPIO_NUM  22

// Frontera del multipart del stream MJPEG ("cuadroUCSM"). El servidor Django
// separa los JPEG por este mismo separador en ReceptorStreamCamara.
static const char* TIPO_STREAM    = "multipart/x-mixed-replace;boundary=cuadroUCSM";
static const char* SEPARADOR      = "\r\n--cuadroUCSM\r\n";
static const char* CABECERA_PARTE = "Content-Type: image/jpeg\r\nContent-Length: %u\r\nX-Timestamp-Ms: %llu\r\n\r\n";

// ============================================================================
//  Camara
// ============================================================================
class Camara {
public:
  bool begin() {
    camera_config_t c = {};
    c.ledc_channel = LEDC_CHANNEL_0;
    c.ledc_timer   = LEDC_TIMER_0;
    c.pin_d0 = Y2_GPIO_NUM; c.pin_d1 = Y3_GPIO_NUM; c.pin_d2 = Y4_GPIO_NUM; c.pin_d3 = Y5_GPIO_NUM;
    c.pin_d4 = Y6_GPIO_NUM; c.pin_d5 = Y7_GPIO_NUM; c.pin_d6 = Y8_GPIO_NUM; c.pin_d7 = Y9_GPIO_NUM;
    c.pin_xclk = XCLK_GPIO_NUM;  c.pin_pclk = PCLK_GPIO_NUM;
    c.pin_vsync = VSYNC_GPIO_NUM; c.pin_href = HREF_GPIO_NUM;
    c.pin_sccb_sda = SIOD_GPIO_NUM; c.pin_sccb_scl = SIOC_GPIO_NUM;
    c.pin_pwdn = PWDN_GPIO_NUM;  c.pin_reset = RESET_GPIO_NUM;
    c.xclk_freq_hz = 20000000;
    c.pixel_format = PIXFORMAT_JPEG;
    c.frame_size   = cfg::RESOLUCION_INICIAL;
    c.jpeg_quality = cfg::CALIDAD_JPEG_INICIAL;
    if (psramFound()) {
      c.fb_count    = 2;                      // 2 buffers en PSRAM (seccion 4)
      c.fb_location = CAMERA_FB_IN_PSRAM;
      c.grab_mode   = CAMERA_GRAB_LATEST;     // siempre el cuadro mas reciente
    } else {
      c.fb_count    = 1;
      c.fb_location = CAMERA_FB_IN_DRAM;
      c.grab_mode   = CAMERA_GRAB_WHEN_EMPTY;
      c.frame_size  = FRAMESIZE_QVGA;
    }

    const esp_err_t err = esp_camera_init(&c);
    if (err != ESP_OK) {
      Serial.printf("[CAM] ERROR esp_camera_init: 0x%x\n", err);
      return false;
    }
    sensor_t* s = esp_camera_sensor_get();
    resolucion_ = c.frame_size;
    calidad_ = c.jpeg_quality;
    if (s) { s->set_framesize(s, resolucion_); s->set_quality(s, calidad_); }
    pinMode(cfg::PIN_FLASH, OUTPUT);
    digitalWrite(cfg::PIN_FLASH, LOW);
    ok_ = true;
    return true;
  }

  // Devuelve un frame (liberar con liberar()). Mide el tiempo de captura.
  camera_fb_t* capturar(bool flash, uint32_t& msCaptura) {
    const uint32_t t0 = millis();
    if (flash) { digitalWrite(cfg::PIN_FLASH, HIGH); delay(80); }
    camera_fb_t* fb = esp_camera_fb_get();
    if (flash && !flashFijo_) digitalWrite(cfg::PIN_FLASH, LOW);
    msCaptura = millis() - t0;
    if (fb) capturas_++; else fallos_++;
    return fb;
  }

  void liberar(camera_fb_t* fb) { if (fb) esp_camera_fb_return(fb); }

  bool configurar(const String& nombreResolucion, int calidad) {
    sensor_t* s = esp_camera_sensor_get();
    if (!s) return false;
    if (nombreResolucion.length()) {
      framesize_t f;
      if (!resolucionDesdeNombre(nombreResolucion, f)) return false;
      if (s->set_framesize(s, f) != 0) return false;
      resolucion_ = f;
    }
    if (calidad >= 10 && calidad <= 63) {
      s->set_quality(s, calidad);
      calidad_ = calidad;
    }
    return true;
  }

  /// Flash permanente (comando MQTT {"flash":true}); distinto del ?flash=1 puntual.
  void setFlashFijo(bool encendido) {
    flashFijo_ = encendido;
    digitalWrite(cfg::PIN_FLASH, encendido ? HIGH : LOW);
  }
  bool flashFijo() const { return flashFijo_; }

  void setFpsMax(int fps) { if (fps >= 1 && fps <= 30) fpsMax_ = fps; }
  int  fpsMax() const { return fpsMax_; }
  /// Milisegundos minimos entre cuadros del stream para respetar fps_max.
  uint32_t periodoCuadroMs() const { return (uint32_t)(1000 / fpsMax_); }

  const char* nombreResolucion() const {
    switch (resolucion_) {
      case FRAMESIZE_QVGA: return "QVGA";
      case FRAMESIZE_VGA:  return "VGA";
      case FRAMESIZE_SVGA: return "SVGA";
      case FRAMESIZE_XGA:  return "XGA";
      case FRAMESIZE_HD:   return "HD";
      default:             return "OTRA";
    }
  }

  bool ok() const { return ok_; }
  int calidad() const { return calidad_; }
  uint32_t capturas() const { return capturas_; }
  uint32_t fallos() const { return fallos_; }

private:
  static bool resolucionDesdeNombre(const String& n, framesize_t& f) {
    if (n == "QVGA") { f = FRAMESIZE_QVGA; return true; }
    if (n == "VGA")  { f = FRAMESIZE_VGA;  return true; }
    if (n == "SVGA") { f = FRAMESIZE_SVGA; return true; }
    if (n == "XGA")  { f = FRAMESIZE_XGA;  return true; }
    if (n == "HD")   { f = FRAMESIZE_HD;   return true; }
    return false;
  }

  bool ok_ = false;
  bool flashFijo_ = false;
  framesize_t resolucion_ = cfg::RESOLUCION_INICIAL;
  int calidad_ = cfg::CALIDAD_JPEG_INICIAL;
  int fpsMax_  = cfg::FPS_MAX_INICIAL;
  uint32_t capturas_ = 0, fallos_ = 0;
};

// ============================================================================
//  UtilHttp  (helpers compartidos por los dos servidores)
// ============================================================================
class UtilHttp {
public:
  /// Lee un parametro de la cadena de consulta (?clave=valor).
  static bool parametro(httpd_req_t* req, const char* clave, char* destino, size_t largo) {
    destino[0] = '\0';
    const size_t largoQuery = httpd_req_get_url_query_len(req) + 1;
    if (largoQuery <= 1 || largoQuery > 256) return false;

    char query[256];
    if (httpd_req_get_url_query_str(req, query, largoQuery) != ESP_OK) return false;
    return httpd_query_key_value(query, clave, destino, largo) == ESP_OK;
  }

  /// Autorizacion por cabecera X-API-Key o por ?key=<clave>.
  static bool autorizado(httpd_req_t* req) {
    char valor[64] = {0};
    const size_t largo = httpd_req_get_hdr_value_len(req, "X-API-Key");
    if (largo > 0 && largo < sizeof(valor) &&
        httpd_req_get_hdr_value_str(req, "X-API-Key", valor, sizeof(valor)) == ESP_OK &&
        strcmp(valor, cfg::API_KEY) == 0)
      return true;

    if (parametro(req, "key", valor, sizeof(valor)) && strcmp(valor, cfg::API_KEY) == 0)
      return true;

    error(req, "401 Unauthorized", "API key invalida");
    return false;
  }

  static void cabecerasComunes(httpd_req_t* req) {
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Device-Code", cfg::CODIGO_CAMARA);
  }

  static void error(httpd_req_t* req, const char* estado, const char* mensaje) {
    JsonDocument d;
    d["ok"] = false;
    d["error"] = mensaje;
    String s;
    serializeJson(d, s);
    httpd_resp_set_status(req, estado);
    httpd_resp_set_type(req, "application/json");
    cabecerasComunes(req);
    httpd_resp_sendstr(req, s.c_str());
  }
};

// ============================================================================
//  ServidorStream  (puerto 81, MJPEG, UN solo cliente a la vez)
//  ---------------------------------------------------------------------------
//  El servidor Django mantiene esta conexion abierta todo el tiempo y guarda
//  los cuadros en un buffer circular de 3 s. Permitir dos clientes partiria el
//  ancho de banda y dejaria a YOLO con la mitad de los cuadros, por eso un
//  segundo cliente recibe 503.
// ============================================================================
class ServidorStream {
public:
  explicit ServidorStream(Camara& cam) : cam_(cam) {}

  bool begin() {
    httpd_config_t conf = HTTPD_DEFAULT_CONFIG();
    conf.server_port = cfg::PUERTO_STREAM;
    conf.ctrl_port   = cfg::PUERTO_STREAM + 1000;   // puerto de control propio
    conf.max_uri_handlers = 2;
    conf.lru_purge_enable = true;
    conf.stack_size  = 8192;

    if (httpd_start(&servidor_, &conf) != ESP_OK) {
      Serial.println("[STREAM] ERROR: no se pudo iniciar el servidor del puerto 81");
      return false;
    }
    httpd_uri_t ruta = { "/stream", HTTP_GET, &ServidorStream::manejador, this };
    httpd_register_uri_handler(servidor_, &ruta);
    return true;
  }

  int      clientes()  const { return clientes_; }
  uint32_t cuadros()   const { return cuadros_; }
  float    fpsReal()   const { return fpsReal_; }

private:
  static esp_err_t manejador(httpd_req_t* req) {
    return static_cast<ServidorStream*>(req->user_ctx)->atender(req);
  }

  esp_err_t atender(httpd_req_t* req) {
    if (!UtilHttp::autorizado(req)) return ESP_OK;

    // Un solo cliente a la vez. OJO con el comportamiento real de
    // esp_http_server: atiende las peticiones en UNA sola tarea, asi que
    // mientras este bucle esta enviando cuadros una segunda peticion queda
    // encolada en el socket y no llega aqui hasta que el primero se va. Esta
    // guarda es la que devuelve el 503 si el handler se reentra (por ejemplo si
    // se configuran mas tareas trabajadoras) y deja el contador en un estado
    // coherente para /status y cam/estado.
    if (clientes_ > 0) {
      UtilHttp::error(req, "503 Service Unavailable",
                      "ya hay un cliente viendo el stream (solo se admite uno)");
      return ESP_OK;
    }
    clientes_ = 1;

    httpd_resp_set_type(req, TIPO_STREAM);
    httpd_resp_set_hdr(req, "Access-Control-Allow-Origin", "*");
    httpd_resp_set_hdr(req, "X-Device-Code", cfg::CODIGO_CAMARA);

    esp_err_t resultado = ESP_OK;
    uint32_t ultimoCuadro = 0;
    uint32_t ultimoConteo = millis();
    uint32_t cuadrosVentana = 0;

    while (resultado == ESP_OK) {
      // Respeta fps_max: dormir aqui es lo que evita saturar el Wi-Fi.
      const uint32_t periodo = cam_.periodoCuadroMs();
      const uint32_t transcurrido = millis() - ultimoCuadro;
      if (transcurrido < periodo) delay(periodo - transcurrido);
      ultimoCuadro = millis();

      uint32_t msCaptura = 0;
      camera_fb_t* fb = cam_.capturar(false, msCaptura);
      if (!fb) { resultado = ESP_FAIL; break; }

      char cabecera[128];
      const int largoCabecera = snprintf(cabecera, sizeof(cabecera), CABECERA_PARTE,
                                         (unsigned)fb->len,
                                         (unsigned long long)(esp_timer_get_time() / 1000));

      resultado = httpd_resp_send_chunk(req, SEPARADOR, strlen(SEPARADOR));
      if (resultado == ESP_OK) resultado = httpd_resp_send_chunk(req, cabecera, largoCabecera);
      if (resultado == ESP_OK) resultado = httpd_resp_send_chunk(req, (const char*)fb->buf, fb->len);
      cam_.liberar(fb);

      cuadros_++;
      cuadrosVentana++;
      if (millis() - ultimoConteo >= 1000) {
        fpsReal_ = cuadrosVentana * 1000.0f / (millis() - ultimoConteo);
        ultimoConteo = millis();
        cuadrosVentana = 0;
      }
    }

    clientes_ = 0;
    fpsReal_ = 0;
    httpd_resp_send_chunk(req, NULL, 0);   // cierra el multipart
    Serial.println("[STREAM] Cliente desconectado.");
    return ESP_OK;
  }

  Camara& cam_;
  httpd_handle_t servidor_ = nullptr;
  volatile int clientes_ = 0;
  uint32_t cuadros_ = 0;
  volatile float fpsReal_ = 0;
};

// ============================================================================
//  ServidorCaptura  (puerto 80: /capture, /status, /config)
// ============================================================================
class ServidorCaptura {
public:
  ServidorCaptura(Camara& cam, ServidorStream& stream) : cam_(cam), stream_(stream) {}

  bool begin() {
    httpd_config_t conf = HTTPD_DEFAULT_CONFIG();
    conf.server_port = cfg::PUERTO_CAPTURA;
    conf.ctrl_port   = cfg::PUERTO_CAPTURA + 1000;
    conf.max_uri_handlers = 6;
    conf.lru_purge_enable = true;
    conf.stack_size  = 8192;

    if (httpd_start(&servidor_, &conf) != ESP_OK) {
      Serial.println("[API] ERROR: no se pudo iniciar el servidor del puerto 80");
      return false;
    }
    registrar("/",        &ServidorCaptura::raizEstatica);
    registrar("/capture", &ServidorCaptura::captureEstatica);
    registrar("/status",  &ServidorCaptura::statusEstatica);
    registrar("/config",  &ServidorCaptura::configEstatica);
    return true;
  }

  /// JSON de estado; lo reutiliza ClienteMQTTCamara para cam/estado.
  String estadoJson(bool enLinea) const {
    JsonDocument d;
    d["ok"]             = cam_.ok();
    d["en_linea"]       = enLinea;
    d["codigo"]         = cfg::CODIGO_CAMARA;
    d["codigo_nodo"]    = cfg::CODIGO_NODO;
    d["firmware"]       = cfg::VERSION_FIRMWARE;
    d["mac"]            = WiFi.macAddress();
    d["ip"]             = WiFi.localIP().toString();
    d["rssi_dbm"]       = WiFi.RSSI();
    d["uptime_ms"]      = (uint32_t)millis();
    d["ram_libre_kb"]   = ESP.getFreeHeap() / 1024;
    d["psram_libre_kb"] = ESP.getFreePsram() / 1024;
    d["resolucion"]     = cam_.nombreResolucion();
    d["calidad_jpeg"]   = cam_.calidad();
    d["fps_max"]        = cam_.fpsMax();
    d["fps_real"]       = round(stream_.fpsReal() * 10) / 10.0;
    d["clientes_stream"] = stream_.clientes();
    d["flash"]          = cam_.flashFijo();
    d["capturas"]       = cam_.capturas();
    d["cuadros_stream"] = stream_.cuadros();
    d["fallos"]         = cam_.fallos();
    d["url_stream"]     = String("http://") + WiFi.localIP().toString() + ":" +
                          String(cfg::PUERTO_STREAM) + "/stream";
    String s;
    serializeJson(d, s);
    return s;
  }

private:
  void registrar(const char* uri, esp_err_t (*manejador)(httpd_req_t*)) {
    httpd_uri_t ruta = { uri, HTTP_GET, manejador, this };
    httpd_register_uri_handler(servidor_, &ruta);
  }

  // --- Trampolines estaticos (esp_http_server trabaja con punteros a funcion) ---
  static esp_err_t raizEstatica(httpd_req_t* r)    { return self(r)->raiz(r); }
  static esp_err_t captureEstatica(httpd_req_t* r) { return self(r)->capture(r); }
  static esp_err_t statusEstatica(httpd_req_t* r)  { return self(r)->status(r); }
  static esp_err_t configEstatica(httpd_req_t* r)  { return self(r)->configurar(r); }
  static ServidorCaptura* self(httpd_req_t* r) { return static_cast<ServidorCaptura*>(r->user_ctx); }

  esp_err_t raiz(httpd_req_t* req) {
    httpd_resp_set_type(req, "text/plain");
    UtilHttp::cabecerasComunes(req);
    httpd_resp_sendstr(req,
      "ESP32-CAM nodo vehicular UCSM v3.0.0\n"
      "Puerto 80: /capture /status /config  (X-API-Key o ?key=)\n"
      "Puerto 81: /stream  (MJPEG, ?key=, un solo cliente)\n");
    return ESP_OK;
  }

  esp_err_t capture(httpd_req_t* req) {
    if (!UtilHttp::autorizado(req)) return ESP_OK;

    char flash[8] = {0};
    UtilHttp::parametro(req, "flash", flash, sizeof(flash));

    uint32_t ms = 0;
    camera_fb_t* fb = cam_.capturar(strcmp(flash, "1") == 0, ms);
    if (!fb) { UtilHttp::error(req, "503 Service Unavailable", "fallo la captura"); return ESP_OK; }

    char ancho[12], alto[12], captura[12], uptime[24];
    snprintf(ancho,   sizeof(ancho),   "%u", (unsigned)fb->width);
    snprintf(alto,    sizeof(alto),    "%u", (unsigned)fb->height);
    snprintf(captura, sizeof(captura), "%lu", (unsigned long)ms);
    snprintf(uptime,  sizeof(uptime),  "%lu", (unsigned long)millis());

    httpd_resp_set_type(req, "image/jpeg");
    UtilHttp::cabecerasComunes(req);
    httpd_resp_set_hdr(req, "X-Frame-Width", ancho);
    httpd_resp_set_hdr(req, "X-Frame-Height", alto);
    httpd_resp_set_hdr(req, "X-Capture-Ms", captura);
    httpd_resp_set_hdr(req, "X-Uptime-Ms", uptime);
    httpd_resp_set_hdr(req, "X-Timestamp-Ms", uptime);
    const esp_err_t r = httpd_resp_send(req, (const char*)fb->buf, fb->len);
    cam_.liberar(fb);
    return r;
  }

  esp_err_t status(httpd_req_t* req) {
    if (!UtilHttp::autorizado(req)) return ESP_OK;
    httpd_resp_set_type(req, "application/json");
    UtilHttp::cabecerasComunes(req);
    httpd_resp_sendstr(req, estadoJson(true).c_str());
    return ESP_OK;
  }

  esp_err_t configurar(httpd_req_t* req) {
    if (!UtilHttp::autorizado(req)) return ESP_OK;

    char resolucion[12] = {0}, calidad[8] = {0}, fps[8] = {0};
    UtilHttp::parametro(req, "resolucion", resolucion, sizeof(resolucion));
    UtilHttp::parametro(req, "calidad", calidad, sizeof(calidad));
    UtilHttp::parametro(req, "fps_max", fps, sizeof(fps));

    const int calidadNum = calidad[0] ? atoi(calidad) : -1;
    if (!cam_.configurar(String(resolucion), calidadNum)) {
      UtilHttp::error(req, "400 Bad Request",
                      "parametros invalidos (resolucion: QVGA|VGA|SVGA|XGA|HD, calidad: 10-63)");
      return ESP_OK;
    }
    if (fps[0]) cam_.setFpsMax(atoi(fps));
    return status(req);
  }

  Camara& cam_;
  ServidorStream& stream_;
  httpd_handle_t servidor_ = nullptr;
};

// ============================================================================
//  ClienteMQTTCamara
//  ---------------------------------------------------------------------------
//  Mismo canal de control que el nodo: publica cam/estado RETENIDO al conectar
//  y cada 30 s, deja un Last Will {"en_linea":false} para que el servidor sepa
//  al instante que la camara cayo, y obedece cam/cmd.
// ============================================================================
class ClienteMQTTCamara {
public:
  ClienteMQTTCamara(Camara& cam, ServidorCaptura& api) : cam_(cam), api_(api) {}

  void begin() {
    snprintf(topicoEstado_, sizeof(topicoEstado_), "%s/%s/cam/estado",
             cfg::PREFIJO_TOPICO, cfg::CODIGO_NODO);
    snprintf(topicoCmd_, sizeof(topicoCmd_), "%s/%s/cam/cmd",
             cfg::PREFIJO_TOPICO, cfg::CODIGO_NODO);
    snprintf(clientId_, sizeof(clientId_), "cam-%s", cfg::CODIGO_NODO);

    esp_mqtt_client_config_t conf = {};
    conf.broker.address.hostname  = cfg::MQTT_HOST;
    conf.broker.address.port       = cfg::MQTT_PUERTO;
    conf.broker.address.transport  = MQTT_TRANSPORT_OVER_TCP;
    conf.credentials.client_id     = clientId_;
    conf.credentials.username      = cfg::MQTT_USUARIO;
    conf.credentials.authentication.password = cfg::MQTT_CLAVE;
    conf.session.keepalive         = cfg::MQTT_KEEPALIVE_S;
    conf.session.disable_clean_session = false;
    conf.session.last_will.topic   = topicoEstado_;
    conf.session.last_will.msg     = "{\"en_linea\":false}";
    conf.session.last_will.msg_len = (int)strlen("{\"en_linea\":false}");
    conf.session.last_will.qos     = 1;
    conf.session.last_will.retain  = 1;
    conf.buffer.size               = 1024;
    conf.buffer.out_size           = 1024;
    conf.network.reconnect_timeout_ms = 2000;

    cliente_ = esp_mqtt_client_init(&conf);
    if (cliente_ == nullptr) {
      Serial.println("[MQTT] ERROR: no se pudo crear el cliente de la camara");
      return;
    }
    esp_mqtt_client_register_event(cliente_, MQTT_EVENT_ANY, &ClienteMQTTCamara::manejador, this);
    esp_mqtt_client_start(cliente_);
  }

  /// Llamar desde loop(): republica el estado retenido cada 30 s.
  void atender() {
    if (!conectado_ || cliente_ == nullptr) return;
    if (millis() - ultimoEstadoMs_ < cfg::ESTADO_PERIODO_MS) return;
    publicarEstado();
  }

  void publicarEstado() {
    if (cliente_ == nullptr) return;
    const String json = api_.estadoJson(true);
    esp_mqtt_client_publish(cliente_, topicoEstado_, json.c_str(), 0, 1, 1);
    ultimoEstadoMs_ = millis();
  }

  bool conectado() const { return conectado_; }

private:
  static void manejador(void* ctx, esp_event_base_t /*base*/, int32_t id, void* datos) {
    static_cast<ClienteMQTTCamara*>(ctx)->alEvento(id, (esp_mqtt_event_handle_t)datos);
  }

  void alEvento(int32_t id, esp_mqtt_event_handle_t e) {
    switch ((esp_mqtt_event_id_t)id) {
      case MQTT_EVENT_CONNECTED:
        conectado_ = true;
        esp_mqtt_client_subscribe(cliente_, topicoCmd_, 1);
        publicarEstado();
        Serial.printf("[MQTT] Conectado como \"%s\"; escuchando %s\n", clientId_, topicoCmd_);
        break;

      case MQTT_EVENT_DISCONNECTED:
        conectado_ = false;
        Serial.println("[MQTT] Desconectado; esp-mqtt reintenta solo.");
        break;

      case MQTT_EVENT_DATA: {
        if (e == nullptr || e->data == nullptr) break;
        char cuerpo[256];
        const int n = e->data_len < (int)sizeof(cuerpo) - 1 ? e->data_len : (int)sizeof(cuerpo) - 1;
        memcpy(cuerpo, e->data, n);
        cuerpo[n] = '\0';
        aplicarComando(cuerpo);
        break;
      }

      case MQTT_EVENT_ERROR:
        Serial.println("[MQTT] Error de transporte (revisa IP, usuario y clave del broker).");
        break;

      default:
        break;
    }
  }

  /// {"resolucion":"VGA","calidad":12,"fps_max":8,"flash":false,"reiniciar":false}
  void aplicarComando(const char* json) {
    JsonDocument doc;
    if (deserializeJson(doc, json)) {
      Serial.println("[MQTT] cam/cmd con JSON invalido.");
      return;
    }

    if (doc["reiniciar"].is<bool>() && doc["reiniciar"].as<bool>()) {
      Serial.println("[MQTT] cam/cmd reiniciar: la camara se reinicia en 300 ms.");
      delay(300);
      ESP.restart();
      return;
    }

    const char* resolucion = doc["resolucion"] | "";
    const int calidad = doc["calidad"] | -1;
    if (resolucion[0] || calidad > 0) {
      const bool ok = cam_.configurar(String(resolucion), calidad);
      Serial.printf("[MQTT] cam/cmd resolucion=\"%s\" calidad=%d -> %s\n",
                    resolucion, calidad, ok ? "aplicado" : "RECHAZADO");
    }
    if (doc["fps_max"].is<int>()) {
      cam_.setFpsMax(doc["fps_max"].as<int>());
      Serial.printf("[MQTT] cam/cmd fps_max=%d\n", cam_.fpsMax());
    }
    if (doc["flash"].is<bool>()) {
      cam_.setFlashFijo(doc["flash"].as<bool>());
      Serial.printf("[MQTT] cam/cmd flash=%s\n", cam_.flashFijo() ? "ON" : "off");
    }
    publicarEstado();
  }

  Camara& cam_;
  ServidorCaptura& api_;
  esp_mqtt_client_handle_t cliente_ = nullptr;
  char topicoEstado_[80] = {};
  char topicoCmd_[80]    = {};
  char clientId_[40]     = {};
  volatile bool conectado_ = false;
  uint32_t ultimoEstadoMs_ = 0;
};

// ============================================================================
//  NodoCamara (orquestador)
// ============================================================================
class NodoCamara {
public:
  NodoCamara() : stream_(camara_), api_(camara_, stream_), mqtt_(camara_, api_) {}

  void setup() {
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);   // evita reinicios por caidas de tension
    Serial.begin(115200);
    delay(500);
    Serial.printf("\n=== ESP32-CAM %s  v%s ===\n", cfg::CODIGO_CAMARA, cfg::VERSION_FIRMWARE);
    Serial.printf("[CAM] PSRAM: %s\n", psramFound() ? "SI" : "NO");
    Serial.println(camara_.begin() ? "[CAM] OV2640 lista" : "[CAM] ERROR: revisa el modulo de camara");

    conectarWiFi();

    stream_.begin();
    api_.begin();
    mqtt_.begin();

    const String ip = WiFi.localIP().toString();
    Serial.printf("[API]    http://%s/capture   (cabecera X-API-Key o ?key=)\n", ip.c_str());
    Serial.printf("[STREAM] http://%s:%u/stream?key=<clave>   (fps_max = %d)\n",
                  ip.c_str(), cfg::PUERTO_STREAM, camara_.fpsMax());
    Serial.printf("[MQTT]   %s:%u  ucsm/v1/%s/cam/...\n",
                  cfg::MQTT_HOST, cfg::MQTT_PUERTO, cfg::CODIGO_NODO);
  }

  void loop() {
    // Los dos servidores HTTP y el cliente MQTT corren en sus propias tareas;
    // aqui solo quedan la reconexion del Wi-Fi y el latido de cam/estado.
    mqtt_.atender();

    if (WiFi.status() != WL_CONNECTED && millis() - ultimoIntento_ > 10000) {
      ultimoIntento_ = millis();
      Serial.println("[WiFi] Reconectando...");
      WiFi.reconnect();
    }

    if (millis() - ultimoResumen_ > 15000) {
      ultimoResumen_ = millis();
      Serial.printf("[ESTADO] %s | clientes_stream=%d fps_real=%.1f cuadros=%lu capturas=%lu "
                    "fallos=%lu | MQTT %s | RAM %u kB PSRAM %u kB\n",
                    camara_.nombreResolucion(), stream_.clientes(), stream_.fpsReal(),
                    (unsigned long)stream_.cuadros(), (unsigned long)camara_.capturas(),
                    (unsigned long)camara_.fallos(), mqtt_.conectado() ? "conectado" : "SIN CONEXION",
                    (unsigned)(ESP.getFreeHeap() / 1024), (unsigned)(ESP.getFreePsram() / 1024));
    }
    delay(20);
  }

private:
  void conectarWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);
    WiFi.setAutoReconnect(true);
    if (cfg::USAR_IP_FIJA) WiFi.config(cfg::IP_FIJA, cfg::GATEWAY, cfg::MASCARA, cfg::DNS);
    WiFi.begin(cfg::WIFI_SSID, cfg::WIFI_PASS);
    Serial.printf("[WiFi] Conectando a \"%s\"", cfg::WIFI_SSID);
    const uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 20000) { delay(500); Serial.print("."); }
    if (WiFi.status() == WL_CONNECTED)
      Serial.printf("\n[WiFi] IP %s  MAC %s  RSSI %d dBm\n",
                    WiFi.localIP().toString().c_str(), WiFi.macAddress().c_str(), WiFi.RSSI());
    else
      Serial.println("\n[WiFi] Sin conexion; se reintentara.");
  }

  Camara            camara_;
  ServidorStream    stream_;
  ServidorCaptura   api_;
  ClienteMQTTCamara mqtt_;
  uint32_t ultimoIntento_ = 0, ultimoResumen_ = 0;
};

NodoCamara nodoCamara;

void setup() { nodoCamara.setup(); }
void loop()  { nodoCamara.loop(); }
