/*
 * ============================================================================
 *  CAMARA DEL NODO VEHICULAR - ESP32-CAM (AI-Thinker, OV2640)       v2.0.0
 *  Tesis: Sistema inteligente IoT con YOLO para monitoreo vehicular (UCSM)
 * ============================================================================
 *  La ESP32-CAM NO decide nada: expone una API HTTP en la LAN y el servidor
 *  Django (main.py) le pide la imagen cuando:
 *    a) el nodo ESP32-S3 reporta un evento (VIBRACION, RUIDO, ...), o
 *    b) la vigilancia visual continua (YOLO) esta activa.
 *
 *  API (todas requieren la cabecera  X-API-Key: <clave>  o  ?key=<clave>):
 *    GET /capture            -> image/jpeg (foto actual)
 *    GET /capture?flash=1    -> igual, encendiendo el LED flash (GPIO4)
 *    GET /status             -> JSON con el estado de la camara
 *    GET /config?resolucion=VGA&calidad=12
 *                            -> cambia resolucion (QVGA, VGA, SVGA, XGA, HD)
 *                               y calidad JPEG (10 = mejor ... 63 = peor)
 *
 *  Cabeceras de /capture (para medir tiempos en el servidor):
 *    X-Device-Code, X-Frame-Width, X-Frame-Height, X-Capture-Ms, X-Uptime-Ms
 *
 *  Placa en Arduino IDE: "AI Thinker ESP32-CAM" (core arduino-esp32 3.x)
 *  Librerias: ArduinoJson 7.x
 *  Alimentacion: 5 V / 2 A estable (los reinicios suelen ser por fuente debil).
 * ============================================================================
 */

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoJson.h>
#include "esp_camera.h"
#include "soc/soc.h"
#include "soc/rtc_cntl_reg.h"

// ============================================================================
//  CONFIGURACION (EDITAR AQUI)
// ============================================================================
namespace cfg {
  const char* WIFI_SSID = "Papitaa";
  const char* WIFI_PASS = "person123";

  const char* API_KEY         = "tesis-ucsm-2026";   // = ESP32CAM_API_KEY en .env
  const char* CODIGO_CAMARA   = "CAM-NODO-01";       // informativo
  const char* VERSION_FIRMWARE = "2.0.0";

  // IP fija recomendada para que config.py siempre apunte a la misma URL.
  // Pon USAR_IP_FIJA = false para usar DHCP (mira la IP en el Monitor Serie).
  constexpr bool USAR_IP_FIJA = true;
  const IPAddress IP_FIJA(10, 144, 55, 120);
  const IPAddress GATEWAY(10, 144, 55, 1);
  const IPAddress MASCARA(255, 255, 255, 0);
  const IPAddress DNS(8, 8, 8, 8);

  constexpr uint16_t PUERTO_HTTP = 80;
  constexpr framesize_t RESOLUCION_INICIAL = FRAMESIZE_VGA;   // 640x480 (bueno para YOLO)
  constexpr int CALIDAD_JPEG_INICIAL = 12;
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
      c.fb_count    = 2;
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
    if (flash) digitalWrite(cfg::PIN_FLASH, LOW);
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
  framesize_t resolucion_ = cfg::RESOLUCION_INICIAL;
  int calidad_ = cfg::CALIDAD_JPEG_INICIAL;
  uint32_t capturas_ = 0, fallos_ = 0;
};

// ============================================================================
//  ServidorAPI
// ============================================================================
class ServidorAPI {
public:
  explicit ServidorAPI(Camara& cam) : cam_(cam), server_(cfg::PUERTO_HTTP) {}

  void begin() {
    static const char* cabeceras[] = { "X-API-Key" };
    server_.collectHeaders(cabeceras, 1);
    server_.on("/",        HTTP_GET, [this]() { raiz(); });
    server_.on("/capture", HTTP_GET, [this]() { capture(); });
    server_.on("/status",  HTTP_GET, [this]() { status(); });
    server_.on("/config",  HTTP_GET, [this]() { configurar(); });
    server_.onNotFound([this]() { error(404, "ruta no encontrada"); });
    server_.begin();
  }

  void atender() { server_.handleClient(); }

private:
  bool autorizado() {
    const String clave = server_.hasHeader("X-API-Key") ? server_.header("X-API-Key") : server_.arg("key");
    if (clave == cfg::API_KEY) return true;
    error(401, "API key invalida");
    return false;
  }

  void cabecerasComunes() {
    server_.sendHeader("Access-Control-Allow-Origin", "*");
    server_.sendHeader("Cache-Control", "no-store");
    server_.sendHeader("X-Device-Code", cfg::CODIGO_CAMARA);
  }

  void error(int codigo, const char* mensaje) {
    JsonDocument d;
    d["ok"] = false;
    d["error"] = mensaje;
    String s;
    serializeJson(d, s);
    cabecerasComunes();
    server_.send(codigo, "application/json", s);
  }

  void raiz() {
    cabecerasComunes();
    server_.send(200, "text/plain",
                 "ESP32-CAM nodo vehicular UCSM. Endpoints: /capture /status /config (requieren X-API-Key)");
  }

  void capture() {
    if (!autorizado()) return;
    uint32_t ms = 0;
    camera_fb_t* fb = cam_.capturar(server_.arg("flash") == "1", ms);
    if (!fb) { error(503, "fallo la captura"); return; }
    cabecerasComunes();
    server_.sendHeader("X-Frame-Width", String(fb->width));
    server_.sendHeader("X-Frame-Height", String(fb->height));
    server_.sendHeader("X-Capture-Ms", String(ms));
    server_.sendHeader("X-Uptime-Ms", String(millis()));
    server_.setContentLength(fb->len);
    server_.send(200, "image/jpeg", "");
    server_.sendContent((const char*)fb->buf, fb->len);
    cam_.liberar(fb);
  }

  void status() {
    if (!autorizado()) return;
    JsonDocument d;
    d["ok"] = cam_.ok();
    d["codigo"] = cfg::CODIGO_CAMARA;
    d["firmware"] = cfg::VERSION_FIRMWARE;
    d["mac"] = WiFi.macAddress();
    d["ip"] = WiFi.localIP().toString();
    d["rssi_dbm"] = WiFi.RSSI();
    d["uptime_ms"] = millis();
    d["ram_libre_kb"] = ESP.getFreeHeap() / 1024;
    d["psram_libre_kb"] = ESP.getFreePsram() / 1024;
    d["resolucion"] = cam_.nombreResolucion();
    d["calidad_jpeg"] = cam_.calidad();
    d["capturas"] = cam_.capturas();
    d["fallos"] = cam_.fallos();
    String s;
    serializeJson(d, s);
    cabecerasComunes();
    server_.send(200, "application/json", s);
  }

  void configurar() {
    if (!autorizado()) return;
    const int calidad = server_.hasArg("calidad") ? server_.arg("calidad").toInt() : -1;
    if (!cam_.configurar(server_.arg("resolucion"), calidad)) {
      error(400, "parametros invalidos (resolucion: QVGA|VGA|SVGA|XGA|HD, calidad: 10-63)");
      return;
    }
    status();
  }

  Camara& cam_;
  WebServer server_;
};

// ============================================================================
//  NodoCamara (orquestador)
// ============================================================================
class NodoCamara {
public:
  NodoCamara() : api_(camara_) {}

  void setup() {
    WRITE_PERI_REG(RTC_CNTL_BROWN_OUT_REG, 0);   // evita reinicios por caidas de tension
    Serial.begin(115200);
    delay(500);
    Serial.printf("\n=== ESP32-CAM %s  v%s ===\n", cfg::CODIGO_CAMARA, cfg::VERSION_FIRMWARE);
    Serial.printf("[CAM] PSRAM: %s\n", psramFound() ? "SI" : "NO");
    Serial.println(camara_.begin() ? "[CAM] OV2640 lista" : "[CAM] ERROR: revisa el modulo de camara");
    conectarWiFi();
    api_.begin();
    Serial.printf("[API] http://%s/capture  (cabecera X-API-Key)\n", WiFi.localIP().toString().c_str());
  }

  void loop() {
    api_.atender();
    if (WiFi.status() != WL_CONNECTED && millis() - ultimoIntento_ > 10000) {
      ultimoIntento_ = millis();
      Serial.println("[WiFi] Reconectando...");
      WiFi.reconnect();
    }
    delay(1);
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

  Camara camara_;
  ServidorAPI api_;
  uint32_t ultimoIntento_ = 0;
};

NodoCamara nodoCamara;

void setup() { nodoCamara.setup(); }
void loop()  { nodoCamara.loop(); }
