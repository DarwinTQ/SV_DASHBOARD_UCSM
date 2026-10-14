/*
 * ============================================================================
 *  NODO IoT VEHICULAR - ESP32-S3                                  v2.0.0
 *  Tesis: Sistema inteligente IoT con YOLO para monitoreo en tiempo real y
 *         alertas ante eventos de riesgo en vehiculos particulares (UCSM)
 * ============================================================================
 *  Sensores:
 *    - GPS NEO-6M      (UART1)  -> posicion, velocidad, altitud, satelites
 *    - MPU-6050        (I2C)    -> aceleracion, vibracion, giro, inclinacion
 *    - INMP441         (I2S)    -> nivel de sonido (dBFS y dB SPL aprox.)
 *    - Bateria (opc.)  (ADC)    -> voltaje y porcentaje (divisor 100k/100k)
 *
 *  Comunicacion con el servidor Django (API REST, JSON, cabecera X-API-Key):
 *    POST /api/v1/telemetria/  cada 1 s  -> panel en tiempo real + tabla
 *                                            estado_dispositivo (heartbeat)
 *    POST /api/v1/eventos/     inmediato -> el servidor pide la foto a la
 *                                            ESP32-CAM, ejecuta YOLO, guarda la
 *                                            incidencia y envia la alerta por
 *                                            Telegram.
 *
 *  Eventos detectados EN EL NODO (codigos = tabla tipo_evento de la BD):
 *    VIBRACION    golpe/impacto: | |a| - media | > umbral (g), a 200 Hz
 *    RUIDO        bloque de audio de 32 ms con nivel > umbral (dB SPL)
 *    MANIPULACION cambio de inclinacion (gata, remolque) o giro brusco
 *    MOVIMIENTO   el GPS reporta velocidad > umbral estando "armado"
 *  (PERSONA lo decide el servidor con YOLO sobre la imagen de la ESP32-CAM.)
 *
 *  Reloj para medir LATENCIAS (tabla medicion_tiempo):
 *    Cada respuesta de la API trae "server_ts_ms". El nodo calcula
 *    offset = server_ts - (t_envio + t_respuesta)/2 quedandose con la muestra
 *    de menor RTT; asi "ts_ms" del evento queda en el MISMO reloj del
 *    servidor (error de pocos ms en LAN). Si aun no hay respuesta del
 *    servidor se usa NTP; si tampoco hay NTP se envia ts_ms = null.
 *
 *  Arquitectura (Programacion Orientada a Objetos + FreeRTOS):
 *    RelojSincronizado  hora en ms sincronizada con el servidor / NTP
 *    DetectorEventos    armado, umbrales, antirrebote (cooldown) y cola
 *    SensorIMU          tarea 200 Hz (nucleo 1)  -> detecta VIBRACION/giro
 *    SensorAudio        tarea 16 kHz (nucleo 1)  -> detecta RUIDO
 *    SensorGPS          decodifica NMEA en loop()
 *    MonitorBateria     voltaje y porcentaje
 *    ClienteAPI         tarea HTTP (nucleo 0): eventos con prioridad y
 *                       reintentos; aplica armado/umbrales que manda el
 *                       servidor
 *    NodoVehicular      orquesta todo y arma los JSON
 *
 *  Requisitos:
 *    - Placa "ESP32S3 Dev Module", core arduino-esp32 3.x (Espressif).
 *    - Librerias (Gestor de librerias):
 *        * TinyGPSPlus  (Mikal Hart)
 *        * ArduinoJson  (Benoit Blanchon) version 7.x
 *    - USB nativo: Herramientas -> USB CDC On Boot: "Enabled".
 * ============================================================================
 */

// timestamps de 64 bits en el JSON
#define ARDUINOJSON_USE_LONG_LONG 1

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <ESP_I2S.h>
#include <TinyGPSPlus.h>
#include <ArduinoJson.h>
#include <esp_timer.h>
#include <sys/time.h>
#include <time.h>
#include <math.h>

// ============================================================================
//  CONFIGURACION (EDITAR AQUI)
// ============================================================================
namespace cfg {
  // ----- Red -----
  const char* WIFI_SSID = "Papitaa";
  const char* WIFI_PASS = "person123";

  // ----- Servidor Django (main.py). IP de la PC: "ipconfig" en Windows -----
  const char* SERVIDOR_BASE   = "http://10.144.55.103:8000";
  const char* RUTA_TELEMETRIA = "/api/v1/telemetria/";
  const char* RUTA_EVENTOS    = "/api/v1/eventos/";
  const char* API_KEY         = "tesis-ucsm-2026";   // = ESP32_API_KEY en .env

  // ----- Identidad: debe existir en la tabla "dispositivo" (campo codigo) ----
  const char* CODIGO_DISPOSITIVO = "NODO-01";
  const char* VERSION_FIRMWARE   = "2.0.0";

  // ----- NTP (respaldo si el servidor aun no respondio) -----
  const char* NTP_1 = "pool.ntp.org";
  const char* NTP_2 = "time.google.com";

  // ----- Pines (segun la tabla de conexiones) -----
  constexpr int GPS_RX_PIN = 18;    // al TX del NEO-6M
  constexpr int GPS_TX_PIN = 17;    // al RX del NEO-6M
  constexpr uint32_t GPS_BAUD = 9600;

  constexpr int I2C_SDA_PIN = 8;
  constexpr int I2C_SCL_PIN = 9;

  constexpr int I2S_SCK_PIN = 4;    // BCLK
  constexpr int I2S_WS_PIN  = 5;    // LRCLK
  constexpr int I2S_SD_PIN  = 6;    // datos

  // ----- Bateria (false si no conectaste el divisor 100k/100k en GPIO1) -----
  constexpr bool  USAR_BATERIA = true;
  constexpr int   BAT_PIN      = 1;
  constexpr float BAT_FACTOR   = 2.0f;  // calibrar: 2.0 * (V_multimetro / V_serial)

  // ----- Muestreo -----
  constexpr uint32_t PERIODO_REPORTE_MS = 1000;
  constexpr uint32_t IMU_HZ             = 200;
  constexpr uint32_t AUDIO_HZ           = 16000;
  constexpr size_t   AUDIO_BLOQUE       = 512;     // 32 ms a 16 kHz
  constexpr float    SPL_OFFSET         = 120.0f;  // INMP441: -26 dBFS @ 94 dB SPL

  // ----- Umbrales por defecto (el servidor puede sobrescribirlos) -----
  constexpr float    UMBRAL_IMPACTO_G       = 0.35f;
  constexpr float    UMBRAL_RUIDO_DB        = 85.0f;
  constexpr float    UMBRAL_INCLINACION_DEG = 3.0f;
  constexpr float    UMBRAL_GIRO_DPS        = 60.0f;
  constexpr float    UMBRAL_MOVIMIENTO_KMH  = 8.0f;
  constexpr uint32_t COOLDOWN_MS            = 15000;  // antirrebote por tipo
  constexpr bool     ARMADO_INICIAL         = true;

  // ----- HTTP -----
  constexpr uint16_t HTTP_TIMEOUT_MS   = 3000;
  constexpr uint8_t  REINTENTOS_EVENTO = 3;
}

// ============================================================================
//  TIPOS COMUNES
// ============================================================================
enum TipoEvento : uint8_t { EV_VIBRACION = 0, EV_RUIDO, EV_MANIPULACION, EV_MOVIMIENTO, EV_TOTAL };
static const char* const CODIGO_EVENTO[EV_TOTAL] = { "VIBRACION", "RUIDO", "MANIPULACION", "MOVIMIENTO" };

struct Umbrales {
  float    impactoG       = cfg::UMBRAL_IMPACTO_G;
  float    ruidoDb        = cfg::UMBRAL_RUIDO_DB;
  float    inclinacionDeg = cfg::UMBRAL_INCLINACION_DEG;
  float    giroDps        = cfg::UMBRAL_GIRO_DPS;
  float    movimientoKmh  = cfg::UMBRAL_MOVIMIENTO_KMH;
  uint32_t cooldownMs     = cfg::COOLDOWN_MS;
};

struct EventoCrudo {          // lo que generan las tareas de sensores
  uint8_t     tipo;
  int64_t     tsMs;           // hora (reloj del servidor) del instante detectado
  float       valor;
  float       umbral;
  const char* unidad;         // cadena constante
};

struct Paquete {              // JSON listo para enviar
  char json[1024];
};

static inline float r2(float v) { return roundf(v * 100.0f) / 100.0f; }
static inline float r4(float v) { return roundf(v * 10000.0f) / 10000.0f; }

// ============================================================================
//  RelojSincronizado
// ============================================================================
class RelojSincronizado {
public:
  void iniciarNTP() { configTime(0, 0, cfg::NTP_1, cfg::NTP_2); }   // UTC

  static int64_t monotonicoMs() { return esp_timer_get_time() / 1000; }

  // Ajuste tipo NTP usando la respuesta HTTP del servidor
  void ajustarConServidor(int64_t serverTsMs, int64_t t0, int64_t t1) {
    if (serverTsMs < 1600000000000LL || t1 < t0) return;
    const int64_t rtt = t1 - t0;
    portENTER_CRITICAL(&mux_);
    if (!sincServidor_ || rtt <= mejorRttMs_ || (t1 - ultimoAjuste_) > 30000) {
      offsetMs_     = serverTsMs - (t0 + t1) / 2;
      mejorRttMs_   = rtt;
      ultimoAjuste_ = t1;
      sincServidor_ = true;
    }
    portEXIT_CRITICAL(&mux_);
  }

  // Hora actual en ms (epoch UTC). 0 si no hay ninguna fuente de hora.
  int64_t ahoraMs() {
    portENTER_CRITICAL(&mux_);
    const bool s = sincServidor_;
    const int64_t off = offsetMs_;
    portEXIT_CRITICAL(&mux_);
    if (s) return monotonicoMs() + off;
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    if (tv.tv_sec > 1600000000) return (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
    return 0;
  }

  const char* fuente() {
    if (sincServidor_) return "SERVIDOR";
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    return tv.tv_sec > 1600000000 ? "NTP" : "NINGUNA";
  }

  int64_t mejorRttMs() const { return mejorRttMs_; }

private:
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  volatile bool sincServidor_ = false;
  int64_t offsetMs_     = 0;
  int64_t mejorRttMs_   = 0;
  int64_t ultimoAjuste_ = 0;
};

// ============================================================================
//  DetectorEventos  (armado, umbrales, cooldown, cola de eventos crudos)
// ============================================================================
class DetectorEventos {
public:
  void begin() {
    cola_ = xQueueCreate(16, sizeof(EventoCrudo));
    for (int i = 0; i < EV_TOTAL; i++) ultimoMs_[i] = -1000000000LL;
  }

  bool armado() const { return armado_; }
  void setArmado(bool a) { armado_ = a; }

  Umbrales umbrales() {
    portENTER_CRITICAL(&mux_);
    Umbrales u = u_;
    portEXIT_CRITICAL(&mux_);
    return u;
  }
  void setUmbrales(const Umbrales& u) {
    portENTER_CRITICAL(&mux_);
    u_ = u;
    portEXIT_CRITICAL(&mux_);
  }

  // Llamado desde las tareas de sensores. Aplica armado y cooldown por tipo.
  bool reportar(TipoEvento tipo, float valor, float umbral, const char* unidad, int64_t tsMs) {
    if (!armado_ || cola_ == nullptr) return false;
    const int64_t ahora = RelojSincronizado::monotonicoMs();
    bool aceptar = false;
    portENTER_CRITICAL(&mux_);
    if (ahora - ultimoMs_[tipo] >= (int64_t)u_.cooldownMs) {
      ultimoMs_[tipo] = ahora;
      aceptar = true;
    }
    portEXIT_CRITICAL(&mux_);
    if (!aceptar) return false;
    EventoCrudo e{ (uint8_t)tipo, tsMs, valor, umbral, unidad };
    if (xQueueSend(cola_, &e, 0) != pdTRUE) { descartados_++; return false; }
    return true;
  }

  bool siguiente(EventoCrudo& e) { return cola_ && xQueueReceive(cola_, &e, 0) == pdTRUE; }
  uint32_t descartados() const { return descartados_; }

private:
  QueueHandle_t cola_ = nullptr;
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  Umbrales u_;
  volatile bool armado_ = cfg::ARMADO_INICIAL;
  int64_t ultimoMs_[EV_TOTAL];
  uint32_t descartados_ = 0;   // contador (escritura en una sola tarea)
};

// ============================================================================
//  SensorIMU  (MPU-6050 por registros, tarea a 200 Hz)
// ============================================================================
struct VentanaIMU {
  uint32_t n = 0, errores = 0;
  float ax = 0, ay = 0, az = 0;      // media (g)
  float aMax = 0;                    // |a| maximo (g)
  float vibRms = 0, vibPp = 0;       // desviacion y pico-pico de |a| (g)
  float giroMax = 0;                 // deg/s
  float temp = 0;                    // C
  float pitch = 0, roll = 0;         // deg
};

class SensorIMU {
public:
  SensorIMU(DetectorEventos& det, RelojSincronizado& reloj) : det_(det), reloj_(reloj) {}

  bool begin() {
    uint8_t who = 0;
    if (!leer(0x75, &who, 1)) return false;                   // WHO_AM_I
    Serial.printf("[IMU] WHO_AM_I = 0x%02X %s\n", who,
                  who == 0x68 ? "(MPU-6050 OK)" : "(compatible, se intenta igual)");
    escribir(0x6B, 0x01); delay(100);   // despertar, reloj PLL
    escribir(0x19, 0x04);               // 1 kHz / 5 = 200 Hz
    escribir(0x1A, 0x02);               // filtro pasa bajos ~94 Hz
    escribir(0x1B, 0x08);               // +-500 deg/s (65.5 LSB/deg/s)
    escribir(0x1C, 0x08);               // +-4 g       (8192 LSB/g)
    reiniciarAcc();
    ok_ = true;
    return true;
  }

  void iniciarTarea() {
    if (ok_) xTaskCreatePinnedToCore(&SensorIMU::trampolin, "imu", 4096, this, 3, nullptr, 1);
  }

  bool ok() const { return ok_; }
  float ultimoModulo() const { return ultimoMod_; }

  VentanaIMU cerrarVentana() {
    Acc a;
    portENTER_CRITICAL(&mux_);
    a = acc_;
    reiniciarAcc();
    portEXIT_CRITICAL(&mux_);

    VentanaIMU v;
    v.n = a.n; v.errores = a.errores;
    if (a.n == 0) return v;
    v.ax = a.sx / a.n; v.ay = a.sy / a.n; v.az = a.sz / a.n;
    const double media = a.sm / a.n;
    const double var = a.sm2 / a.n - media * media;
    v.vibRms  = var > 0 ? sqrt(var) : 0;
    v.vibPp   = a.mmax - a.mmin;
    v.aMax    = a.mmax;
    v.giroMax = a.gmax;
    v.temp    = a.st / a.n;
    v.pitch   = atan2f(-v.ax, sqrtf(v.ay * v.ay + v.az * v.az)) * 57.29578f;
    v.roll    = atan2f(v.ay, v.az) * 57.29578f;
    return v;
  }

private:
  struct Acc {
    uint32_t n = 0, errores = 0;
    double sx = 0, sy = 0, sz = 0, sm = 0, sm2 = 0, st = 0;
    float mmin = 99, mmax = 0, gmax = 0;
  };

  static constexpr uint8_t DIR = 0x68;   // AD0 a GND

  static void trampolin(void* p) { static_cast<SensorIMU*>(p)->tarea(); }

  void reiniciarAcc() {
    const uint32_t err = acc_.errores;
    acc_ = Acc();
    acc_.errores = err;
  }

  bool escribir(uint8_t reg, uint8_t val) {
    Wire.beginTransmission(DIR);
    Wire.write(reg);
    Wire.write(val);
    return Wire.endTransmission() == 0;
  }

  bool leer(uint8_t reg, uint8_t* buf, size_t len) {
    Wire.beginTransmission(DIR);
    Wire.write(reg);
    if (Wire.endTransmission(false) != 0) return false;
    if (Wire.requestFrom(DIR, len, true) != len) return false;
    for (size_t i = 0; i < len; i++) buf[i] = Wire.read();
    return true;
  }

  void tarea() {
    uint8_t b[14];
    uint32_t muestras = 0;
    TickType_t ultimo = xTaskGetTickCount();
    const TickType_t periodo = pdMS_TO_TICKS(1000 / cfg::IMU_HZ);
    for (;;) {
      vTaskDelayUntil(&ultimo, periodo);
      if (!leer(0x3B, b, 14)) {
        portENTER_CRITICAL(&mux_); acc_.errores++; portEXIT_CRITICAL(&mux_);
        continue;
      }
      const int16_t rax = (b[0] << 8) | b[1],  ray = (b[2] << 8) | b[3],  raz = (b[4] << 8) | b[5];
      const int16_t rt  = (b[6] << 8) | b[7];
      const int16_t rgx = (b[8] << 8) | b[9],  rgy = (b[10] << 8) | b[11], rgz = (b[12] << 8) | b[13];

      const float ax = rax / 8192.0f, ay = ray / 8192.0f, az = raz / 8192.0f;
      const float gx = rgx / 65.5f,   gy = rgy / 65.5f,   gz = rgz / 65.5f;
      const float t  = rt / 340.0f + 36.53f;
      const float m  = sqrtf(ax * ax + ay * ay + az * az);   // |a| en g
      const float gm = sqrtf(gx * gx + gy * gy + gz * gz);   // |w| en deg/s
      ultimoMod_ = m;

      portENTER_CRITICAL(&mux_);
      acc_.n++;
      acc_.sx += ax; acc_.sy += ay; acc_.sz += az;
      acc_.sm += m;  acc_.sm2 += (double)m * m; acc_.st += t;
      if (m < acc_.mmin) acc_.mmin = m;
      if (m > acc_.mmax) acc_.mmax = m;
      if (gm > acc_.gmax) acc_.gmax = gm;
      portEXIT_CRITICAL(&mux_);

      // --- Deteccion inmediata (no espera la ventana de 1 s) ---
      mediaMod_ += 0.005f * (m - mediaMod_);     // media movil exponencial de |a|
      if (++muestras < 2 * cfg::IMU_HZ) continue; // 2 s de calentamiento
      const Umbrales u = det_.umbrales();
      const float desvio = fabsf(m - mediaMod_);
      if (desvio > u.impactoG)
        det_.reportar(EV_VIBRACION, desvio, u.impactoG, "g", reloj_.ahoraMs());
      if (gm > u.giroDps)
        det_.reportar(EV_MANIPULACION, gm, u.giroDps, "deg/s", reloj_.ahoraMs());
    }
  }

  DetectorEventos& det_;
  RelojSincronizado& reloj_;
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  Acc acc_;
  bool ok_ = false;
  float mediaMod_ = 1.0f;
  volatile float ultimoMod_ = 0;
};

// ============================================================================
//  SensorAudio  (INMP441 por I2S, 24 bits en palabras de 32, canal izquierdo)
// ============================================================================
struct VentanaAudio {
  uint32_t n = 0;
  float rmsDbfs = -120, splDb = 0, picoDbfs = -120, picoSplDb = 0;
};

class SensorAudio {
public:
  SensorAudio(DetectorEventos& det, RelojSincronizado& reloj) : det_(det), reloj_(reloj) {}

  bool begin() {
    i2s_.setPins(cfg::I2S_SCK_PIN, cfg::I2S_WS_PIN, -1, cfg::I2S_SD_PIN);
    ok_ = i2s_.begin(I2S_MODE_STD, cfg::AUDIO_HZ, I2S_DATA_BIT_WIDTH_32BIT,
                     I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
    return ok_;
  }

  void iniciarTarea() {
    if (ok_) xTaskCreatePinnedToCore(&SensorAudio::trampolin, "audio", 4096, this, 2, nullptr, 1);
  }

  bool ok() const { return ok_; }
  float ultimoSplDb() const { return ultimoSpl_; }

  VentanaAudio cerrarVentana() {
    uint32_t n; double sumsq; float pico;
    portENTER_CRITICAL(&mux_);
    n = n_; sumsq = sumsq_; pico = pico_;
    n_ = 0; sumsq_ = 0; pico_ = 0;
    portEXIT_CRITICAL(&mux_);

    VentanaAudio v;
    v.n = n;
    if (n > 0) {
      const float rms = sqrt(sumsq / n);
      if (rms > 0)  v.rmsDbfs  = 20.0f * log10f(rms / FS);
      if (pico > 0) v.picoDbfs = 20.0f * log10f(pico / FS);
    }
    v.splDb     = v.rmsDbfs + cfg::SPL_OFFSET;
    v.picoSplDb = v.picoDbfs + cfg::SPL_OFFSET;
    return v;
  }

private:
  static constexpr float FS = 8388608.0f;   // 2^23, fondo de escala 24 bits

  static void trampolin(void* p) { static_cast<SensorAudio*>(p)->tarea(); }

  void tarea() {
    uint32_t bloques = 0;
    for (;;) {
      const size_t bytes = i2s_.readBytes((char*)buf_, sizeof(buf_));
      const size_t n = bytes / sizeof(int32_t);
      if (n == 0) { vTaskDelay(1); continue; }

      double media = 0;
      for (size_t i = 0; i < n; i++) media += (buf_[i] >> 8);
      media /= n;

      double sumsq = 0; float pico = 0;
      for (size_t i = 0; i < n; i++) {
        const double v = (double)(buf_[i] >> 8) - media;
        sumsq += v * v;
        const float av = fabs(v);
        if (av > pico) pico = av;
      }

      portENTER_CRITICAL(&mux_);
      n_ += n; sumsq_ += sumsq;
      if (pico > pico_) pico_ = pico;
      portEXIT_CRITICAL(&mux_);

      // --- Deteccion por bloque de 32 ms ---
      const float rmsBloque = sqrt(sumsq / n);
      const float splBloque = rmsBloque > 0 ? 20.0f * log10f(rmsBloque / FS) + cfg::SPL_OFFSET : 0;
      ultimoSpl_ = splBloque;
      if (++bloques < 30) continue;   // ~1 s de estabilizacion del microfono
      const Umbrales u = det_.umbrales();
      if (splBloque > u.ruidoDb)
        det_.reportar(EV_RUIDO, splBloque, u.ruidoDb, "dB SPL", reloj_.ahoraMs());
    }
  }

  DetectorEventos& det_;
  RelojSincronizado& reloj_;
  I2SClass i2s_;
  bool ok_ = false;
  int32_t buf_[cfg::AUDIO_BLOQUE];
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  uint32_t n_ = 0;
  double sumsq_ = 0;
  float pico_ = 0;
  volatile float ultimoSpl_ = 0;
};

// ============================================================================
//  SensorGPS
// ============================================================================
struct LecturaGPS {
  bool fix = false;
  double lat = 0, lon = 0;
  float velKmh = 0, altM = 0, hdop = 99.9f;
  int sats = 0;
  uint32_t caracteres = 0;
};

class SensorGPS {
public:
  SensorGPS() : serial_(1) {}
  void begin() { serial_.begin(cfg::GPS_BAUD, SERIAL_8N1, cfg::GPS_RX_PIN, cfg::GPS_TX_PIN); }
  void actualizar() { while (serial_.available()) gps_.encode(serial_.read()); }

  LecturaGPS lectura() {
    LecturaGPS l;
    l.fix    = gps_.location.isValid() && gps_.location.age() < 3000;
    l.lat    = gps_.location.lat();
    l.lon    = gps_.location.lng();
    l.velKmh = gps_.speed.isValid() ? gps_.speed.kmph() : 0;
    l.altM   = gps_.altitude.isValid() ? gps_.altitude.meters() : 0;
    l.sats   = gps_.satellites.isValid() ? gps_.satellites.value() : 0;
    l.hdop   = gps_.hdop.isValid() ? gps_.hdop.hdop() : 99.9f;
    l.caracteres = gps_.charsProcessed();
    return l;
  }

private:
  TinyGPSPlus gps_;
  HardwareSerial serial_;
};

// ============================================================================
//  MonitorBateria
// ============================================================================
class MonitorBateria {
public:
  void begin() { if (cfg::USAR_BATERIA) analogSetPinAttenuation(cfg::BAT_PIN, ADC_11db); }

  float voltaje() {
    if (!cfg::USAR_BATERIA) return 0;
    uint32_t suma = 0;
    for (int i = 0; i < 16; i++) suma += analogReadMilliVolts(cfg::BAT_PIN);
    return (suma / 16.0f) * cfg::BAT_FACTOR / 1000.0f;
  }

  static int porcentaje(float v) {
    static const float volt[] = {4.20, 4.10, 4.00, 3.90, 3.80, 3.70, 3.60, 3.50, 3.40, 3.30};
    static const int   pct[]  = { 100,   90,   80,   65,   50,   35,   20,   10,    5,    0};
    if (v >= volt[0]) return 100;
    if (v <= volt[9]) return 0;
    for (int i = 0; i < 9; i++)
      if (v >= volt[i + 1])
        return pct[i + 1] + (v - volt[i + 1]) * (pct[i] - pct[i + 1]) / (volt[i] - volt[i + 1]);
    return 0;
  }
};

// ============================================================================
//  ClienteAPI  (tarea HTTP en el nucleo 0; eventos primero, con reintentos)
// ============================================================================
class ClienteAPI {
public:
  ClienteAPI(RelojSincronizado& reloj, DetectorEventos& det) : reloj_(reloj), det_(det) {}

  void begin() {
    colaTelemetria_ = xQueueCreate(4, sizeof(Paquete));
    colaEventos_    = xQueueCreate(8, sizeof(Paquete));
    xTaskCreatePinnedToCore(&ClienteAPI::trampolin, "api", 8192, this, 1, nullptr, 0);
  }

  bool encolarTelemetria(const Paquete& p) {
    if (xQueueSend(colaTelemetria_, &p, 0) == pdTRUE) return true;
    descartados_++;
    return false;
  }
  bool encolarEvento(const Paquete& p) {
    if (xQueueSend(colaEventos_, &p, 0) == pdTRUE) return true;
    descartados_++;
    return false;
  }

  int      ultimoCodigo() const { return ultimoCodigo_; }
  uint32_t ultimoRttMs()  const { return ultimoRtt_; }
  uint32_t ok()           const { return ok_; }
  uint32_t fallos()       const { return fallos_; }
  uint32_t descartados()  const { return descartados_; }
  uint32_t eventosEnviados() const { return eventosOk_; }

private:
  static void trampolin(void* p) { static_cast<ClienteAPI*>(p)->tarea(); }

  void tarea() {
    for (;;) {
      if (xQueueReceive(colaEventos_, &buf_, 0) == pdTRUE) {
        for (uint8_t i = 1; i <= cfg::REINTENTOS_EVENTO; i++) {
          const int c = enviar(cfg::RUTA_EVENTOS, buf_.json);
          if (c >= 200 && c < 300) { eventosOk_++; break; }
          vTaskDelay(pdMS_TO_TICKS(300 * i));
        }
        continue;
      }
      if (xQueueReceive(colaTelemetria_, &buf_, pdMS_TO_TICKS(20)) == pdTRUE)
        enviar(cfg::RUTA_TELEMETRIA, buf_.json);
    }
  }

  int enviar(const char* ruta, const char* json) {
    if (WiFi.status() != WL_CONNECTED) { ultimoCodigo_ = -100; fallos_++; return -100; }

    HTTPClient http;
    http.setConnectTimeout(cfg::HTTP_TIMEOUT_MS);
    http.setTimeout(cfg::HTTP_TIMEOUT_MS);
    const String url = String(cfg::SERVIDOR_BASE) + ruta;
    if (!http.begin(url)) { ultimoCodigo_ = -101; fallos_++; return -101; }
    http.addHeader("Content-Type", "application/json");
    http.addHeader("X-API-Key", cfg::API_KEY);
    http.addHeader("X-Device-Code", cfg::CODIGO_DISPOSITIVO);

    const int64_t t0 = RelojSincronizado::monotonicoMs();
    const int codigo = http.POST((uint8_t*)json, strlen(json));
    const int64_t t1 = RelojSincronizado::monotonicoMs();
    ultimoRtt_ = (uint32_t)(t1 - t0);
    ultimoCodigo_ = codigo;

    if (codigo >= 200 && codigo < 300) {
      ok_++;
      procesarRespuesta(http.getString(), t0, t1);
    } else {
      fallos_++;
    }
    http.end();
    return codigo;
  }

  // Respuesta: {"ok":true,"server_ts_ms":..., "armado":true, "umbrales":{...}}
  void procesarRespuesta(const String& cuerpo, int64_t t0, int64_t t1) {
    JsonDocument doc;
    if (deserializeJson(doc, cuerpo)) return;

    const int64_t serverTs = doc["server_ts_ms"] | (int64_t)0;
    if (serverTs > 0) reloj_.ajustarConServidor(serverTs, t0, t1);

    if (doc["armado"].is<bool>()) det_.setArmado(doc["armado"].as<bool>());

    JsonObjectConst j = doc["umbrales"];
    if (!j.isNull()) {
      Umbrales u = det_.umbrales();
      u.impactoG       = j["impacto_g"]       | u.impactoG;
      u.ruidoDb        = j["ruido_db"]        | u.ruidoDb;
      u.inclinacionDeg = j["inclinacion_deg"] | u.inclinacionDeg;
      u.giroDps        = j["giro_dps"]        | u.giroDps;
      u.movimientoKmh  = j["movimiento_kmh"]  | u.movimientoKmh;
      const float cdS  = j["cooldown_s"]      | (u.cooldownMs / 1000.0f);
      u.cooldownMs     = (uint32_t)(cdS * 1000.0f);
      det_.setUmbrales(u);
    }
  }

  RelojSincronizado& reloj_;
  DetectorEventos& det_;
  QueueHandle_t colaTelemetria_ = nullptr, colaEventos_ = nullptr;
  Paquete buf_;
  volatile int ultimoCodigo_ = 0;
  uint32_t ultimoRtt_ = 0, ok_ = 0, fallos_ = 0, descartados_ = 0, eventosOk_ = 0;  // contadores
};

// ============================================================================
//  NodoVehicular  (orquestador)
// ============================================================================
class NodoVehicular {
public:
  NodoVehicular()
    : imu_(detector_, reloj_), audio_(detector_, reloj_), api_(reloj_, detector_) {}

  void setup() {
    Serial.begin(115200);
    delay(1500);
    Serial.printf("\n=== NODO IoT VEHICULAR ESP32-S3  v%s  (%s) ===\n",
                  cfg::VERSION_FIRMWARE, cfg::CODIGO_DISPOSITIVO);
    bootId_ = esp_random();

    detector_.begin();

    gps_.begin();
    Serial.println("[GPS] UART1 iniciado (9600 baudios)");

    Wire.begin(cfg::I2C_SDA_PIN, cfg::I2C_SCL_PIN, 400000);
    Serial.println(imu_.begin() ? "[IMU] MPU-6050: +-4 g, +-500 deg/s, 200 Hz"
                                : "[IMU] ERROR: MPU-6050 no responde en 0x68");
    Serial.println(audio_.begin() ? "[AUDIO] INMP441 a 16 kHz"
                                  : "[AUDIO] ERROR al iniciar I2S");
    bateria_.begin();

    conectarWiFi();
    reloj_.iniciarNTP();
    api_.begin();
    imu_.iniciarTarea();
    audio_.iniciarTarea();

    Serial.printf("[API] %s%s  |  %s%s\n", cfg::SERVIDOR_BASE, cfg::RUTA_TELEMETRIA,
                  cfg::SERVIDOR_BASE, cfg::RUTA_EVENTOS);
    proximoReporte_ = millis() + cfg::PERIODO_REPORTE_MS;
  }

  void loop() {
    gps_.actualizar();

    EventoCrudo e;
    while (detector_.siguiente(e)) enviarEvento(e);

    if ((int32_t)(millis() - proximoReporte_) >= 0) {
      proximoReporte_ += cfg::PERIODO_REPORTE_MS;
      reporteTelemetria();
    }
    delay(2);
  }

private:
  void conectarWiFi() {
    WiFi.mode(WIFI_STA);
    WiFi.setSleep(false);          // menor latencia
    WiFi.setAutoReconnect(true);
    WiFi.begin(cfg::WIFI_SSID, cfg::WIFI_PASS);
    Serial.printf("[WiFi] Conectando a \"%s\"", cfg::WIFI_SSID);
    const uint32_t t0 = millis();
    while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) { delay(500); Serial.print("."); }
    if (WiFi.status() == WL_CONNECTED)
      Serial.printf("\n[WiFi] Conectado. IP %s  MAC %s  RSSI %d dBm\n",
                    WiFi.localIP().toString().c_str(), WiFi.macAddress().c_str(), WiFi.RSSI());
    else
      Serial.println("\n[WiFi] Sin conexion por ahora; se reintentara en segundo plano.");
  }

  int rssi() { return WiFi.status() == WL_CONNECTED ? WiFi.RSSI() : 0; }

  // ---------- Bloques JSON reutilizables ----------
  void jsonGps(JsonObject o, const LecturaGPS& g) {
    o["fix"] = g.fix;
    o["sats"] = g.sats;
    if (!g.fix) return;
    o["lat"] = g.lat;
    o["lon"] = g.lon;
    o["vel_kmh"] = r2(g.velKmh);
    o["alt_m"] = r2(g.altM);
    o["hdop"] = r2(g.hdop);
  }

  void jsonBateria(JsonDocument& doc, float v) {
    if (!cfg::USAR_BATERIA) { doc["bateria"] = nullptr; return; }
    JsonObject b = doc["bateria"].to<JsonObject>();
    b["v"] = r2(v);
    b["pct"] = MonitorBateria::porcentaje(v);
  }

  void jsonTiempo(JsonDocument& doc, int64_t ts) {
    if (ts > 0) doc["ts_ms"] = ts; else doc["ts_ms"] = nullptr;
    doc["ts_fuente"] = reloj_.fuente();
  }

  // ---------- Evento -> POST /api/v1/eventos/ ----------
  void enviarEvento(const EventoCrudo& e) {
    const LecturaGPS g = gps_.lectura();
    JsonDocument doc;
    doc["codigo"] = cfg::CODIGO_DISPOSITIVO;
    char uid[32];
    snprintf(uid, sizeof(uid), "%08lX-%lu", (unsigned long)bootId_, (unsigned long)++numEvento_);
    doc["evento_uid"] = uid;
    doc["tipo"] = CODIGO_EVENTO[e.tipo];
    jsonTiempo(doc, e.tsMs);
    doc["valor"] = r4(e.valor);
    doc["umbral"] = r4(e.umbral);
    doc["unidad"] = e.unidad;
    doc["aceleracion_g"] = imu_.ok() ? r4(imu_.ultimoModulo()) : 0;
    doc["nivel_audio_db"] = audio_.ok() ? r2(audio_.ultimoSplDb()) : 0;
    jsonGps(doc["gps"].to<JsonObject>(), g);
    JsonObject im = doc["imu"].to<JsonObject>();
    im["pitch_deg"] = r2(ultimaVentana_.pitch);
    im["roll_deg"]  = r2(ultimaVentana_.roll);
    doc["wifi"]["rssi_dbm"] = rssi();
    jsonBateria(doc, ultimoVoltaje_);

    Paquete p;
    serializeJson(doc, p.json, sizeof(p.json));
    const bool ok = api_.encolarEvento(p);
    Serial.printf(">>> EVENTO %s  valor=%.3f %s (umbral %.3f)  uid=%s  %s\n",
                  CODIGO_EVENTO[e.tipo], e.valor, e.unidad, e.umbral, uid,
                  ok ? "-> encolado" : "-> COLA LLENA");
  }

  // ---------- Telemetria cada 1 s -> POST /api/v1/telemetria/ ----------
  void reporteTelemetria() {
    seq_++;
    const VentanaIMU im = imu_.ok() ? imu_.cerrarVentana() : VentanaIMU();
    const VentanaAudio au = audio_.ok() ? audio_.cerrarVentana() : VentanaAudio();
    const LecturaGPS g = gps_.lectura();
    ultimoVoltaje_ = bateria_.voltaje();
    if (im.n > 0) ultimaVentana_ = im;

    detectarPorVentana(im, g);

    JsonDocument doc;
    doc["codigo"]   = cfg::CODIGO_DISPOSITIVO;
    doc["firmware"] = cfg::VERSION_FIRMWARE;
    doc["mac"]      = WiFi.macAddress();
    doc["ip"]       = WiFi.localIP().toString();
    doc["boot_id"]  = bootId_;
    doc["seq"]      = seq_;
    doc["uptime_ms"] = (int64_t)RelojSincronizado::monotonicoMs();
    jsonTiempo(doc, reloj_.ahoraMs());
    doc["armado"]   = detector_.armado();

    doc["wifi"]["rssi_dbm"] = rssi();
    JsonObject sis = doc["sistema"].to<JsonObject>();
    sis["ram_libre_kb"] = ESP.getFreeHeap() / 1024;
    sis["temp_c"] = imu_.ok() && im.n > 0 ? r2(im.temp) : r2(temperatureRead());

    jsonGps(doc["gps"].to<JsonObject>(), g);

    if (imu_.ok() && im.n > 0) {
      JsonObject o = doc["imu"].to<JsonObject>();
      o["ax"] = r4(im.ax); o["ay"] = r4(im.ay); o["az"] = r4(im.az);
      o["a_max_g"] = r4(im.aMax);
      o["vib_rms_g"] = r4(im.vibRms);
      o["vib_pp_g"] = r4(im.vibPp);
      o["giro_max_dps"] = r2(im.giroMax);
      o["pitch_deg"] = r2(im.pitch);
      o["roll_deg"] = r2(im.roll);
      o["muestras"] = im.n;
    } else {
      doc["imu"] = nullptr;
    }

    if (audio_.ok() && au.n > 0) {
      JsonObject o = doc["audio"].to<JsonObject>();
      o["rms_dbfs"] = r2(au.rmsDbfs);
      o["spl_db"] = r2(au.splDb);
      o["pico_dbfs"] = r2(au.picoDbfs);
      o["pico_spl_db"] = r2(au.picoSplDb);
      o["muestras"] = au.n;
    } else {
      doc["audio"] = nullptr;
    }

    jsonBateria(doc, ultimoVoltaje_);

    JsonObject h = doc["http"].to<JsonObject>();
    h["rtt_prev_ms"] = api_.ultimoRttMs();
    h["ok"] = api_.ok();
    h["fallos"] = api_.fallos();
    h["descartados"] = api_.descartados() + detector_.descartados();

    Paquete p;
    const size_t len = serializeJson(doc, p.json, sizeof(p.json));
    if (len >= sizeof(p.json) - 1) Serial.println("[JSON] AVISO: telemetria truncada");
    api_.encolarTelemetria(p);

    imprimir(im, au, g);
  }

  // Inclinacion (ventana de 1 s) y movimiento por GPS
  void detectarPorVentana(const VentanaIMU& im, const LecturaGPS& g) {
    const Umbrales u = detector_.umbrales();

    if (im.n > 0) {
      if (!baseValida_ || !detector_.armado()) {
        // Desarmado: la referencia sigue la posicion actual del vehiculo
        basePitch_ = im.pitch; baseRoll_ = im.roll;
        baseValida_ = seq_ > 3;
      } else {
        const float d = fmaxf(fabsf(im.pitch - basePitch_), fabsf(im.roll - baseRoll_));
        if (d > u.inclinacionDeg) {
          detector_.reportar(EV_MANIPULACION, d, u.inclinacionDeg, "deg", reloj_.ahoraMs());
          basePitch_ = im.pitch; baseRoll_ = im.roll;   // nueva referencia
        }
      }
    }

    if (g.fix && g.velKmh > u.movimientoKmh) {
      if (++segundosEnMovimiento_ >= 3)
        detector_.reportar(EV_MOVIMIENTO, g.velKmh, u.movimientoKmh, "km/h", reloj_.ahoraMs());
    } else {
      segundosEnMovimiento_ = 0;
    }
  }

  void imprimir(const VentanaIMU& im, const VentanaAudio& au, const LecturaGPS& g) {
    Serial.printf("\n===== #%lu | %s | reloj=%s (rtt min %lld ms) | WiFi %d dBm =====\n",
                  (unsigned long)seq_, detector_.armado() ? "ARMADO" : "desarmado",
                  reloj_.fuente(), (long long)reloj_.mejorRttMs(), rssi());
    if (g.fix)
      Serial.printf("GPS   : FIX lat=%.6f lon=%.6f vel=%.1f km/h sats=%d hdop=%.1f\n",
                    g.lat, g.lon, g.velKmh, g.sats, g.hdop);
    else
      Serial.printf("GPS   : sin fix (sats=%d, chars=%lu)%s\n", g.sats, (unsigned long)g.caracteres,
                    g.caracteres < 10 && millis() > 5000 ? "  <-- revisa TX/RX del GPS" : "");
    if (imu_.ok())
      Serial.printf("IMU   : |a|max=%.3f g vib_pp=%.4f g giro=%.1f dps pitch=%.1f roll=%.1f T=%.1f C (%lu)\n",
                    im.aMax, im.vibPp, im.giroMax, im.pitch, im.roll, im.temp, (unsigned long)im.n);
    else
      Serial.println("IMU   : MPU-6050 no detectado (SDA=8, SCL=9, VCC, GND)");
    if (audio_.ok())
      Serial.printf("AUDIO : %.1f dB SPL  pico %.1f dB SPL (%lu)\n", au.splDb, au.picoSplDb, (unsigned long)au.n);
    if (cfg::USAR_BATERIA)
      Serial.printf("BAT   : %.2f V (%d %%)\n", ultimoVoltaje_, MonitorBateria::porcentaje(ultimoVoltaje_));
    Serial.printf("HTTP  : cod=%d rtt=%lu ms | OK=%lu fallos=%lu descartados=%lu eventos=%lu\n",
                  api_.ultimoCodigo(), (unsigned long)api_.ultimoRttMs(), (unsigned long)api_.ok(),
                  (unsigned long)api_.fallos(), (unsigned long)api_.descartados(),
                  (unsigned long)api_.eventosEnviados());
  }

  RelojSincronizado reloj_;
  DetectorEventos   detector_;
  SensorIMU         imu_;
  SensorAudio       audio_;
  SensorGPS         gps_;
  MonitorBateria    bateria_;
  ClienteAPI        api_;

  uint32_t bootId_ = 0, seq_ = 0, numEvento_ = 0, proximoReporte_ = 0;
  VentanaIMU ultimaVentana_;
  float ultimoVoltaje_ = 0;
  bool baseValida_ = false;
  float basePitch_ = 0, baseRoll_ = 0;
  uint8_t segundosEnMovimiento_ = 0;
};

// ============================================================================
//  PUNTO DE ENTRADA
// ============================================================================
NodoVehicular nodo;

void setup() { nodo.setup(); }
void loop()  { nodo.loop(); }
