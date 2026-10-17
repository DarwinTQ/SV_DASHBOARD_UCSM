/*
 * ============================================================================
 *  NODO IoT VEHICULAR - ESP32-S3                                  v3.0.0
 *  Tesis: Sistema inteligente IoT con YOLO para monitoreo en tiempo real y
 *         alertas ante eventos de riesgo en vehiculos particulares (UCSM)
 * ============================================================================
 *  QUE CAMBIA RESPECTO A v2.0.0
 *    - El transporte ya NO es HTTP: todo va por MQTT contra el broker
 *      Mosquitto de la PC (conexion persistente, 2 bytes de cabecera, QoS por
 *      tipo de mensaje y Last Will para que el servidor sepa al instante que
 *      el nodo se desconecto). Se eliminaron /api/v1/telemetria/ y
 *      /api/v1/eventos/ del firmware.
 *    - Reglas de deteccion definitivas (CHOQUE y ROTURA DE VENTANA) basadas en
 *      el MPU-6050 a +-16 g / 500 Hz y en el analisis espectral del INMP441
 *      (FFT de 256 puntos). La logica vive en reglas_deteccion.h, que se
 *      prueba en la PC sin hardware.
 *    - Modo del vehiculo (EN_MARCHA / ESTACIONADO) por senal de contacto o por
 *      regla inercial; condiciona que reglas estan activas.
 *    - GPS NEO-6M a 5 Hz (configurado por UBX) y publicacion de posicion a
 *      5 Hz en marcha / 1 Hz estacionado.
 *
 *  SENSORES
 *    - GPS NEO-6M      (UART1)  -> posicion a 5 Hz, velocidad, rumbo, HDOP
 *    - MPU-6050        (I2C)    -> |a| a 500 Hz, +-16 g, vibracion, giro
 *    - INMP441         (I2S)    -> 16 kHz, bloques de 256 muestras (16 ms),
 *                                  dB SPL + FFT (energia por banda, planitud)
 *    - Contacto ACC/IGN (opc.)  -> modo del vehiculo (ver cfg::USAR_CONTACTO)
 *    - Bateria (opc.)  (ADC)    -> voltaje y porcentaje (divisor 100k/100k)
 *
 *  TOPICOS MQTT  (prefijo = cfg::PREFIJO_TOPICO + "/" + cfg::CODIGO_DISPOSITIVO)
 *    PUBLICA                                QoS  Retenido
 *      .../telemetria   cada 1 s             0      no
 *      .../gps          5 Hz / 1 Hz          0      no
 *      .../evento       inmediato            1      no
 *      .../estado       al conectar + LWT    1      SI
 *      .../modo         al cambiar           1      SI
 *      .../sync/req     cada 30 s            0      no
 *    SE SUSCRIBE
 *      .../config       {"armado":..,"umbrales":{..}}   (retenido)
 *      .../cmd          {"accion":"reiniciar"|"recalibrar_piso"|"forzar_modo"}
 *      .../sync/resp    {"id":..,"t0":..,"server_ts_ms":..}
 *
 *  RELOJ PARA MEDIR LATENCIAS (tabla medicion_tiempo)
 *    El nodo publica sync/req con {id, t0} (ms monotonicos) y el servidor
 *    responde de inmediato con server_ts_ms. Con t1 = llegada de la respuesta:
 *        rtt    = t1 - t0
 *        offset = server_ts_ms - (t0 + t1) / 2
 *    Se conserva la muestra de MENOR RTT dentro de una ventana de 60 s, asi
 *    "ts_ms" queda en el MISMO reloj del servidor (pocos ms de error en LAN).
 *    Respaldo NTP; si no hay ninguna fuente se publica ts_ms = null.
 *    IMPORTANTE: las reglas trabajan siempre con el reloj MONOTONICO (nunca
 *    salta) y el instante se convierte a epoca solo al publicar.
 *
 *  ARQUITECTURA (POO + FreeRTOS)
 *    RelojSincronizado   hora en ms sincronizada por MQTT / NTP
 *    ClienteMQTT         esp-mqtt en su propia tarea + 2 colas (eventos QoS 1
 *                        con prioridad, datos QoS 0 que descartan lo mas viejo)
 *    TransformadaEspDsp  FFT acelerada con esp-dsp (respaldo: TransformadaRadix2)
 *    OrquestadorReglas   dueño de ReglaChoque / ReglaRotura / MaquinaModo;
 *                        unico punto de acceso protegido desde las 3 tareas
 *    SensorIMU           tarea 500 Hz (nucleo 1, prioridad 4)
 *    SensorAudio         tarea 16 kHz + FFT (nucleo 1, prioridad 3)
 *    SensorGPS           NEO-6M a 5 Hz, decodifica NMEA en loop()
 *    MonitorBateria      voltaje y porcentaje
 *    NodoVehicular       orquesta todo y arma los JSON
 *
 *  REQUISITOS
 *    - Placa "ESP32S3 Dev Module", core arduino-esp32 3.x (Espressif).
 *    - Librerias (Gestor de librerias):
 *        * TinyGPSPlus  (Mikal Hart)
 *        * ArduinoJson  (Benoit Blanchon) version 7.x
 *        * esp-dsp      (opcional; si no esta, se usa la FFT interna)
 *    - USB nativo: Herramientas -> USB CDC On Boot: "Enabled".
 *    - El broker debe estar corriendo: infra\mosquitto\iniciar_mosquitto.bat
 *
 *  NOTA SOBRE EL CLIENTE MQTT
 *    Se usa el cliente nativo de ESP-IDF ("mqtt_client.h"), incluido en el core
 *    arduino-esp32 3.x: es asincrono, corre en su propia tarea, soporta QoS 1,
 *    Last Will y buffer configurable. Si tu core no lo trae, reemplaza la clase
 *    ClienteMQTT por PubSubClient con setBufferSize(2048) manteniendo la misma
 *    interfaz publica (conectar/publicar/suscribir) y documenta el cambio.
 * ============================================================================
 */

// timestamps de 64 bits en el JSON
#define ARDUINOJSON_USE_LONG_LONG 1

#include <Arduino.h>

// loop() arma los JSON (telemetria de ~1 KB) y encola paquetes de 1.4 KB: con
// los 8 KB por defecto de la tarea Arduino el margen queda muy justo.
SET_LOOP_TASK_STACK_SIZE(16 * 1024);

#include <WiFi.h>
#include <Wire.h>
#include <ESP_I2S.h>
#include <TinyGPSPlus.h>
#include <ArduinoJson.h>
#include <esp_timer.h>
#include <esp_system.h>
#include <sys/time.h>
#include <time.h>
#include <math.h>

#include "mqtt_client.h"
#include "reglas_deteccion.h"

// esp-dsp es opcional: si esta instalada se usa su FFT (mucho mas rapida en el
// ESP32-S3); si no, TransformadaRadix2 del encabezado hace el trabajo.
#if __has_include("esp_dsp.h")
  #include "esp_dsp.h"
  #define TIENE_ESP_DSP 1
#else
  #define TIENE_ESP_DSP 0
#endif

// ============================================================================
//  CONFIGURACION (EDITAR AQUI)
// ============================================================================
namespace cfg {
  // ----- Red -----
  const char* WIFI_SSID = "Papitaa";
  const char* WIFI_PASS = "person123";

  // ----- Broker MQTT (la PC donde corre main.py). IP: "ipconfig" en Windows --
  const char* MQTT_HOST       = "10.189.44.103";   //cambiar por la IP de la PC
  constexpr uint16_t MQTT_PUERTO = 1883;
  const char* MQTT_USUARIO    = "nodo01";          // = usuario de infra\mosquitto\passwd
  const char* MQTT_CLAVE      = "ucsm2026nodo";    // CAMBIAR en produccion
  const char* PREFIJO_TOPICO  = "ucsm/v1";         // = MQTT_PREFIJO en .env
  constexpr uint16_t MQTT_KEEPALIVE_S = 15;
  constexpr size_t   MQTT_BUFFER = 2048;

  // ----- Identidad: debe existir en la tabla "dispositivo" (campo codigo) ----
  const char* CODIGO_DISPOSITIVO = "NODO-01";
  const char* VERSION_FIRMWARE   = "3.0.0";

  // ----- NTP (respaldo si el servidor aun no respondio) -----
  const char* NTP_1 = "pool.ntp.org";
  const char* NTP_2 = "time.google.com";

  // ----- Pines (segun la tabla de conexiones) -----
  constexpr int GPS_RX_PIN = 18;    // al TX del NEO-6M
  constexpr int GPS_TX_PIN = 17;    // al RX del NEO-6M
  constexpr uint32_t GPS_BAUD_INICIAL = 9600;
  constexpr uint32_t GPS_BAUD_RAPIDO  = 38400;   // necesario para 5 Hz holgado

  constexpr int I2C_SDA_PIN = 8;
  constexpr int I2C_SCL_PIN = 9;

  constexpr int I2S_SCK_PIN = 4;    // BCLK
  constexpr int I2S_WS_PIN  = 5;    // LRCLK
  constexpr int I2S_SD_PIN  = 6;    // datos

  // ----- Senal de contacto ACC/IGN (opcional, seccion 3.3) -------------------
  //  El ACC del vehiculo son 12 V y el ESP32-S3 trabaja a 3.3 V: NUNCA lo
  //  conectes directo. Dos opciones, ambas validas:
  //
  //  (a) RECOMENDADA - optoacoplador PC817 (aislamiento galvanico total):
  //        ACC 12 V --[ R 1k ]--> anodo (pin 1) PC817
  //        catodo (pin 2) PC817 --> GND del vehiculo
  //        colector (pin 4) PC817 --> PIN_CONTACTO del ESP32 (INPUT_PULLUP)
  //        emisor (pin 3) PC817   --> GND del ESP32
  //      Con el contacto ENCENDIDO el fototransistor conduce y el pin lee LOW,
  //      por eso CONTACTO_ACTIVO_BAJO = true.
  //
  //  (b) Divisor resistivo + zener de 3.3 V (mas simple, sin aislamiento):
  //        ACC 12 V --[ R 10k ]--+--[ R 3.3k ]-- GND     (da ~2.97 V)
  //                              +--|<|-- GND            (zener 3.3 V de recorte)
  //                              +------> PIN_CONTACTO (INPUT)
  //      Aqui el pin lee HIGH con el contacto encendido: pon
  //      CONTACTO_ACTIVO_BAJO = false.
  //
  //  Mientras USAR_CONTACTO sea false manda la regla inercial (fuente INERCIAL).
  constexpr bool USAR_CONTACTO        = false;
  constexpr int  PIN_CONTACTO         = 10;
  constexpr bool CONTACTO_ACTIVO_BAJO = true;   // true para el PC817 de (a)

  // ----- Bateria (false si no conectaste el divisor 100k/100k en GPIO1) -----
  constexpr bool  USAR_BATERIA = true;
  constexpr int   BAT_PIN      = 1;
  constexpr float BAT_FACTOR   = 2.0f;  // calibrar: 2.0 * (V_multimetro / V_serial)

  // ----- Muestreo -----
  constexpr uint32_t PERIODO_REPORTE_MS = 1000;   // telemetria
  constexpr uint32_t IMU_HZ             = 500;    // SMPLRT_DIV = 1
  constexpr uint32_t AUDIO_HZ           = audio_cfg::MUESTREO_HZ;   // 16000
  constexpr size_t   AUDIO_BLOQUE       = audio_cfg::FFT_PUNTOS;    // 256 (16 ms)

  // ----- GPS: frecuencia de publicacion segun el modo (seccion 3.5) ---------
  constexpr uint32_t GPS_PERIODO_MARCHA_MS      = 200;   // 5 Hz
  constexpr uint32_t GPS_PERIODO_ESTACIONADO_MS = 1000;  // 1 Hz
  constexpr float    GPS_HDOP_MAXIMO            = 5.0f;  // no publicar peor que esto

  // ----- Umbrales por defecto de las reglas definitivas (seccion 3.7) -------
  //  El servidor puede sobrescribirlos publicando .../config (retenido).
  constexpr float    CHOQUE_G              = 4.0f;
  constexpr float    CHOQUE_DB             = 112.0f;
  constexpr uint32_t CHOQUE_VENTANA_MS     = 500;
  constexpr float    GOLPE_BAJA_DB         = 15.0f;
  constexpr float    ESTALLIDO_ALTA_DB     = 12.0f;
  constexpr float    PLANITUD_MIN          = 0.3f;
  constexpr uint32_t ESTALLIDO_VENTANA_MS  = 300;
  constexpr uint32_t ESTALLIDO_MIN_MS      = 150;
  constexpr uint32_t FUSION_MS             = 500;
  constexpr float    VIBRACION_G           = 0.15f;
  constexpr uint32_t GOLPE_SIN_ESTALLIDO_MS = 1000;
  constexpr float    QUIETO_G              = 0.05f;
  constexpr uint32_t QUIETO_S              = 300;
  constexpr uint32_t MARCHA_S              = 10;
  constexpr bool     ARMADO_INICIAL        = true;

  // ----- Reglas legado (v2.0.0): MANIPULACION por inclinacion y MOVIMIENTO ---
  //  por GPS. Quedan DESACTIVADAS para respetar las reglas definitivas.
  constexpr bool     REGLAS_LEGADO         = false;
  constexpr float    LEGADO_INCLINACION_DEG = 3.0f;
  constexpr float    LEGADO_MOVIMIENTO_KMH  = 8.0f;
  constexpr uint32_t LEGADO_COOLDOWN_MS     = 15000;

  // ----- Sincronizacion de reloj -----
  constexpr uint32_t SYNC_PERIODO_MS   = 30000;  // cada 30 s
  constexpr uint8_t  SYNC_AL_CONECTAR  = 5;      // 5 veces seguidas al conectar
  constexpr uint32_t SYNC_VENTANA_MS   = 60000;  // ventana del "mejor RTT"
}

// ============================================================================
//  TIPOS COMUNES
// ============================================================================
/// Sufijos de topico que el nodo publica (indice -> cadena).
enum SufijoTopico : uint8_t {
  TOP_TELEMETRIA = 0, TOP_GPS, TOP_EVENTO, TOP_ESTADO, TOP_MODO, TOP_SYNC_REQ, TOP_TOTAL
};
static const char* const SUFIJO_TOPICO[TOP_TOTAL] = {
  "telemetria", "gps", "evento", "estado", "modo", "sync/req"
};

/// Niveles de alerta que viajan en el evento (seccion 3.4).
static const char* const NIVEL_ALERTA      = "ALERTA";
static const char* const NIVEL_ALERTA_LEVE = "ALERTA_LEVE";
static const char* const NIVEL_REGISTRO    = "REGISTRO";

/// Paquete listo para publicar (lo que viaja por las colas de FreeRTOS).
struct Paquete {
  uint8_t  sufijo = TOP_TELEMETRIA;
  uint8_t  qos    = 0;
  bool     retener = false;
  uint16_t largo  = 0;
  char     json[1400];   // la telemetria completa ocupa ~1 KB (tope: 2048 del broker)
};

/// Evento ya decidido por las reglas, pendiente de serializar en loop().
struct EventoDetectado {
  char            tipo[24]  = {0};
  const char*     nivel     = NIVEL_REGISTRO;
  int64_t         tsMonoMs  = 0;      // reloj MONOTONICO (se convierte al publicar)
  bool            esChoque  = false;
  EvidenciaChoque evChoque;
  EvidenciaRotura evRotura;
  float           aceleracionG = 0;
  float           nivelAudioDb = 0;
  Modo            modo      = Modo::EN_MARCHA;
  bool            armado    = true;
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

  /// Registra una peticion sync/req pendiente (para validar la respuesta).
  uint32_t nuevaPeticion(int64_t& t0) {
    t0 = monotonicoMs();
    portENTER_CRITICAL(&mux_);
    const uint32_t id = ++ultimoId_;
    pendientes_[id % PENDIENTES_MAX] = t0;
    portEXIT_CRITICAL(&mux_);
    return id;
  }

  /**
   * Procesa sync/resp. Conserva la muestra de MENOR RTT de los ultimos 60 s.
   * @return true si la muestra se adopto como nueva referencia.
   */
  bool procesarRespuesta(uint32_t id, int64_t t0Eco, int64_t serverTsMs) {
    if (serverTsMs < 1600000000000LL) return false;
    const int64_t t1 = monotonicoMs();

    portENTER_CRITICAL(&mux_);
    const int64_t t0Guardado = pendientes_[id % PENDIENTES_MAX];
    portEXIT_CRITICAL(&mux_);
    if (t0Guardado == 0) return false;                  // id desconocido
    const int64_t t0 = t0Eco > 0 ? t0Eco : t0Guardado;
    if (t0 != t0Guardado) return false;                 // eco inconsistente
    if (t1 < t0) return false;

    const int64_t rtt = t1 - t0;
    bool adoptada = false;
    portENTER_CRITICAL(&mux_);
    if (t1 - inicioVentana_ > (int64_t)cfg::SYNC_VENTANA_MS) {
      inicioVentana_ = t1;          // nueva ventana: se vuelve a buscar el mejor RTT
      mejorRttMs_    = INT64_MAX;
    }
    if (!sincServidor_ || rtt <= mejorRttMs_) {
      offsetMs_     = serverTsMs - (t0 + t1) / 2;
      mejorRttMs_   = rtt;
      ultimoRttMs_  = rtt;
      sincServidor_ = true;
      adoptada      = true;
    } else {
      ultimoRttMs_ = rtt;
    }
    portEXIT_CRITICAL(&mux_);
    return adoptada;
  }

  /// Hora actual en ms (epoch UTC). 0 si no hay ninguna fuente de hora.
  int64_t ahoraMs() { return aEpocaMs(monotonicoMs()); }

  /**
   * Convierte un instante del reloj MONOTONICO a epoca UTC en ms.
   * Las reglas trabajan en monotonico (nunca salta) y solo al publicar se
   * traduce, de modo que un ajuste de reloj no deforma las ventanas de tiempo.
   */
  int64_t aEpocaMs(int64_t monoMs) {
    portENTER_CRITICAL(&mux_);
    const bool s = sincServidor_;
    const int64_t off = offsetMs_;
    portEXIT_CRITICAL(&mux_);
    if (s) return monoMs + off;

    struct timeval tv;
    gettimeofday(&tv, nullptr);
    if (tv.tv_sec > 1600000000) {
      const int64_t epocaAhora = (int64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000;
      return epocaAhora - (monotonicoMs() - monoMs);
    }
    return 0;
  }

  const char* fuente() {
    if (sincServidor_) return "SERVIDOR";
    struct timeval tv;
    gettimeofday(&tv, nullptr);
    return tv.tv_sec > 1600000000 ? "NTP" : "NINGUNA";
  }

  bool    sincronizado() const { return sincServidor_; }
  int64_t mejorRttMs()   const { return mejorRttMs_ == INT64_MAX ? 0 : mejorRttMs_; }
  int64_t ultimoRttMs()  const { return ultimoRttMs_; }

private:
  static constexpr size_t PENDIENTES_MAX = 16;

  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  volatile bool sincServidor_ = false;
  int64_t offsetMs_      = 0;
  int64_t mejorRttMs_    = INT64_MAX;
  int64_t ultimoRttMs_   = 0;
  int64_t inicioVentana_ = 0;
  uint32_t ultimoId_     = 0;
  int64_t pendientes_[PENDIENTES_MAX] = {0};
};

// ============================================================================
//  TransformadaEspDsp  (FFT acelerada; respaldo a TransformadaRadix2)
// ============================================================================
class TransformadaEspDsp : public TransformadaBase {
public:
  bool begin() {
#if TIENE_ESP_DSP
    ok_ = (dsps_fft2r_init_fc32(NULL, (int)audio_cfg::FFT_PUNTOS) == ESP_OK);
#else
    ok_ = false;
#endif
    return ok_;
  }

  bool acelerada() const { return ok_; }

  void transformar(float* re, float* im, size_t n) override {
#if TIENE_ESP_DSP
    if (ok_ && n == audio_cfg::FFT_PUNTOS) {
      // esp-dsp trabaja con un arreglo complejo intercalado [re0,im0,re1,im1,...]
      for (size_t i = 0; i < n; i++) { intercalado_[2 * i] = re[i]; intercalado_[2 * i + 1] = im[i]; }
      dsps_fft2r_fc32(intercalado_, (int)n);
      dsps_bit_rev_fc32(intercalado_, (int)n);
      for (size_t i = 0; i < n; i++) { re[i] = intercalado_[2 * i]; im[i] = intercalado_[2 * i + 1]; }
      return;
    }
#endif
    respaldo_.transformar(re, im, n);
  }

private:
  bool ok_ = false;
  TransformadaRadix2 respaldo_;
#if TIENE_ESP_DSP
  float intercalado_[2 * audio_cfg::FFT_PUNTOS] = {0};
#endif
};

// ============================================================================
//  OrquestadorReglas
//  ---------------------------------------------------------------------------
//  Dueño de las tres reglas y unico punto de acceso a ellas. Lo tocan TRES
//  contextos distintos (tarea IMU a 500 Hz, tarea de audio cada 16 ms y
//  loop()), por eso cada metodo entra en una seccion critica muy corta: las
//  reglas solo hacen comparaciones, el trabajo pesado (FFT, JSON, MQTT) queda
//  siempre fuera del lock.
//
//  Las decisiones no se publican aqui: se encolan y loop() las serializa.
// ============================================================================
class OrquestadorReglas {
public:
  void begin(const UmbralesDeteccion& u) {
    cola_ = xQueueCreate(8, sizeof(EventoDetectado));
    portENTER_CRITICAL(&mux_);
    umbrales_ = u;
    choque_.setUmbrales(u);
    rotura_.setUmbrales(u);
    modo_.setUmbrales(u);
    modo_.setUsarContacto(cfg::USAR_CONTACTO);
    portEXIT_CRITICAL(&mux_);
  }

  // ---------------------- Configuracion remota ----------------------------
  void setUmbrales(const UmbralesDeteccion& u) {
    portENTER_CRITICAL(&mux_);
    umbrales_ = u;
    choque_.setUmbrales(u);
    rotura_.setUmbrales(u);
    modo_.setUmbrales(u);
    portEXIT_CRITICAL(&mux_);
  }

  UmbralesDeteccion umbrales() {
    portENTER_CRITICAL(&mux_);
    const UmbralesDeteccion u = umbrales_;
    portEXIT_CRITICAL(&mux_);
    return u;
  }

  void setArmado(bool a) { armado_ = a; }
  bool armado() const    { return armado_; }

  Modo modo() {
    portENTER_CRITICAL(&mux_);
    const Modo m = modo_.modo();
    portEXIT_CRITICAL(&mux_);
    return m;
  }

  FuenteModo fuenteModo() {
    portENTER_CRITICAL(&mux_);
    const FuenteModo f = modo_.fuente();
    portEXIT_CRITICAL(&mux_);
    return f;
  }

  /// true (una sola vez) si el modo cambio y hay que publicar .../modo.
  bool consumirCambioDeModo() {
    portENTER_CRITICAL(&mux_);
    const bool cambio = modoCambio_;
    modoCambio_ = false;
    portEXIT_CRITICAL(&mux_);
    return cambio;
  }

  bool forzarModo(Modo nuevo) {
    portENTER_CRITICAL(&mux_);
    const bool cambio = modo_.forzarModo(nuevo);
    if (cambio) modoCambio_ = true;
    portEXIT_CRITICAL(&mux_);
    return cambio;
  }

  void recalibrarPiso() {
    portENTER_CRITICAL(&mux_);
    rotura_.recalibrarPiso();
    portEXIT_CRITICAL(&mux_);
  }

  // ---------------------- Entradas de los sensores ------------------------
  /// Desde la tarea IMU, 500 veces por segundo.
  void muestraIMU(float moduloG, float vibracionG, int64_t tsMonoMs) {
    EventoDetectado evento;
    bool hayEvento = false;

    portENTER_CRITICAL(&mux_);
    // La Regla 1 solo corre EN_MARCHA, pero se evalua aunque este desarmado
    // (un choque es una emergencia, no un robo).
    if (modo_.modo() == Modo::EN_MARCHA) {
      choque_.muestraAceleracion(moduloG, tsMonoMs);
      EvidenciaChoque ev;
      const DecisionChoque d = choque_.evaluar(tsMonoMs, ev);
      if (d != DecisionChoque::NINGUNA) {
        hayEvento = true;
        armarEventoChoque(evento, d, ev);
      }
    }
    // La vibracion alimenta la Regla 2 (fusion con el estallido acustico).
    rotura_.registrarVibracion(vibracionG, tsMonoMs);
    portEXIT_CRITICAL(&mux_);

    if (hayEvento) encolar(evento);
  }

  /// Desde la tarea de audio, cada 16 ms (la FFT ya se calculo fuera del lock).
  void bloqueAudio(const ResultadoBloque& b) {
    EventoDetectado evento;
    bool hayEvento = false;
    const bool roturaHabilitada = armado_ && (modo() == Modo::ESTACIONADO);

    portENTER_CRITICAL(&mux_);
    ultimoSplDb_ = b.splDb;
    if (b.splDb > splPicoDb_) splPicoDb_ = b.splDb;

    if (modo_.modo() == Modo::EN_MARCHA) {
      choque_.bloqueAudio(b.splDb, b.tsMs);
      EvidenciaChoque ev;
      const DecisionChoque d = choque_.evaluar(b.tsMs, ev);
      if (d != DecisionChoque::NINGUNA) {
        hayEvento = true;
        armarEventoChoque(evento, d, ev);
      }
    }

    EvidenciaRotura evR;
    const DecisionRotura dR = rotura_.bloque(b, roturaHabilitada, evR);
    if (!hayEvento && dR != DecisionRotura::NINGUNA) {
      hayEvento = true;
      armarEventoRotura(evento, dR, evR);
    }
    portEXIT_CRITICAL(&mux_);

    if (hayEvento) encolar(evento);
  }

  /// Desde loop(), una vez por segundo: ventana de 1 s para MaquinaModo.
  void ventanaIMU(float variacionG, int64_t tsMonoMs) {
    portENTER_CRITICAL(&mux_);
    if (modo_.ventana(variacionG, tsMonoMs)) modoCambio_ = true;
    portEXIT_CRITICAL(&mux_);
  }

  /// Desde loop(): lectura del pin de contacto (tiene prioridad sobre lo inercial).
  void senalContacto(bool encendido, int64_t tsMonoMs) {
    portENTER_CRITICAL(&mux_);
    if (modo_.senalContacto(encendido, tsMonoMs)) modoCambio_ = true;
    portEXIT_CRITICAL(&mux_);
  }

  /// Desde loop(): temporizadores que no dependen de que llegue una muestra.
  void evaluarTemporizadores(int64_t tsMonoMs) {
    EventoDetectado evento;
    bool hayEvento = false;

    portENTER_CRITICAL(&mux_);
    if (modo_.modo() == Modo::EN_MARCHA) {
      EvidenciaChoque ev;
      const DecisionChoque d = choque_.evaluar(tsMonoMs, ev);
      if (d != DecisionChoque::NINGUNA) { hayEvento = true; armarEventoChoque(evento, d, ev); }
    }
    if (!hayEvento && armado_ && modo_.modo() == Modo::ESTACIONADO) {
      EvidenciaRotura evR;
      const DecisionRotura dR = rotura_.evaluar(tsMonoMs, evR);
      if (dR != DecisionRotura::NINGUNA) { hayEvento = true; armarEventoRotura(evento, dR, evR); }
    }
    portEXIT_CRITICAL(&mux_);

    if (hayEvento) encolar(evento);
  }

  // ---------------------- Salidas -----------------------------------------
  bool siguienteEvento(EventoDetectado& e) {
    return cola_ && xQueueReceive(cola_, &e, 0) == pdTRUE;
  }

  /// Pico de dB SPL desde la ultima llamada (para la telemetria de 1 s).
  float cerrarSplPico() {
    portENTER_CRITICAL(&mux_);
    const float pico = splPicoDb_;
    splPicoDb_ = 0;
    portEXIT_CRITICAL(&mux_);
    return pico;
  }

  float ultimoSplDb() {
    portENTER_CRITICAL(&mux_);
    const float v = ultimoSplDb_;
    portEXIT_CRITICAL(&mux_);
    return v;
  }

  float pisoBajaDb() {
    portENTER_CRITICAL(&mux_);
    const float v = rotura_.pisoBaja().valorDb();
    portEXIT_CRITICAL(&mux_);
    return v;
  }

  float pisoAltaDb() {
    portENTER_CRITICAL(&mux_);
    const float v = rotura_.pisoAlta().valorDb();
    portEXIT_CRITICAL(&mux_);
    return v;
  }

  bool pisoListo() {
    portENTER_CRITICAL(&mux_);
    const bool v = rotura_.pisoBaja().listo() && rotura_.pisoAlta().listo();
    portEXIT_CRITICAL(&mux_);
    return v;
  }

  uint32_t eventosDescartados() const { return descartados_; }

private:
  /// Se llama DENTRO de la seccion critica.
  void armarEventoChoque(EventoDetectado& e, DecisionChoque d, const EvidenciaChoque& ev) {
    const bool confirmado = (d == DecisionChoque::CHOQUE);
    strlcpy(e.tipo, confirmado ? "CHOQUE" : "IMPACTO_SIN_CONFIRMAR", sizeof(e.tipo));
    e.nivel        = confirmado ? NIVEL_ALERTA : NIVEL_REGISTRO;
    e.tsMonoMs     = ev.tsMs;
    e.esChoque     = true;
    e.evChoque     = ev;
    e.aceleracionG = ev.aPicoG;
    e.nivelAudioDb = confirmado ? ev.splPicoDb : ultimoSplDb_;
    e.modo         = modo_.modo();
    e.armado       = armado_;
  }

  /// Se llama DENTRO de la seccion critica.
  void armarEventoRotura(EventoDetectado& e, DecisionRotura d, const EvidenciaRotura& ev) {
    switch (d) {
      case DecisionRotura::ROTURA_VENTANA:
        strlcpy(e.tipo, "ROTURA_VENTANA", sizeof(e.tipo));      e.nivel = NIVEL_ALERTA;      break;
      case DecisionRotura::VIDRIO_ROTO_EXTERNO:
        strlcpy(e.tipo, "VIDRIO_ROTO_EXTERNO", sizeof(e.tipo)); e.nivel = NIVEL_REGISTRO;    break;
      case DecisionRotura::GOLPE_MANIPULACION:
        strlcpy(e.tipo, "GOLPE_MANIPULACION", sizeof(e.tipo));  e.nivel = NIVEL_ALERTA_LEVE; break;
      default: return;
    }
    e.tsMonoMs     = ev.tsMs;
    e.esChoque     = false;
    e.evRotura     = ev;
    e.aceleracionG = ev.hayVib ? ev.vibG : 0;
    e.nivelAudioDb = ultimoSplDb_;
    e.modo         = modo_.modo();
    e.armado       = armado_;
  }

  void encolar(const EventoDetectado& e) {
    if (cola_ == nullptr || xQueueSend(cola_, &e, 0) != pdTRUE) descartados_++;
  }

  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  UmbralesDeteccion umbrales_;
  ReglaChoque  choque_;
  ReglaRotura  rotura_;
  MaquinaModo  modo_;

  QueueHandle_t cola_ = nullptr;
  volatile bool armado_ = cfg::ARMADO_INICIAL;
  bool     modoCambio_  = false;
  float    splPicoDb_   = 0;
  float    ultimoSplDb_ = 0;
  uint32_t descartados_ = 0;
};

// ============================================================================
//  ClienteMQTT  (esp-mqtt en su propia tarea; colas separadas por prioridad)
// ============================================================================
class ClienteMQTT {
public:
  /// Callback que recibe los mensajes de config / cmd / sync/resp.
  using Receptor = void (*)(void* contexto, const char* sufijo, const char* json, size_t largo);

  void begin(Receptor receptor, void* contexto) {
    receptor_  = receptor;
    contexto_  = contexto;

    for (uint8_t i = 0; i < TOP_TOTAL; i++)
      snprintf(topicos_[i], sizeof(topicos_[i]), "%s/%s/%s",
               cfg::PREFIJO_TOPICO, cfg::CODIGO_DISPOSITIVO, SUFIJO_TOPICO[i]);
    snprintf(topicoConfig_,   sizeof(topicoConfig_),   "%s/%s/config",
             cfg::PREFIJO_TOPICO, cfg::CODIGO_DISPOSITIVO);
    snprintf(topicoCmd_,      sizeof(topicoCmd_),      "%s/%s/cmd",
             cfg::PREFIJO_TOPICO, cfg::CODIGO_DISPOSITIVO);
    snprintf(topicoSyncResp_, sizeof(topicoSyncResp_), "%s/%s/sync/resp",
             cfg::PREFIJO_TOPICO, cfg::CODIGO_DISPOSITIVO);
    snprintf(clientId_,       sizeof(clientId_),       "nodo-%s", cfg::CODIGO_DISPOSITIVO);

    colaEventos_ = xQueueCreate(6, sizeof(Paquete));
    colaDatos_   = xQueueCreate(6, sizeof(Paquete));

    esp_mqtt_client_config_t conf = {};
    conf.broker.address.hostname  = cfg::MQTT_HOST;
    conf.broker.address.port      = cfg::MQTT_PUERTO;
    conf.broker.address.transport = MQTT_TRANSPORT_OVER_TCP;
    conf.credentials.client_id    = clientId_;
    conf.credentials.username     = cfg::MQTT_USUARIO;
    conf.credentials.authentication.password = cfg::MQTT_CLAVE;
    conf.session.keepalive        = cfg::MQTT_KEEPALIVE_S;
    conf.session.disable_clean_session = false;        // clean_session = true
    // Last Will retenido: si el nodo cae, el servidor lo marca fuera de linea
    // al instante sin esperar ningun timeout.
    conf.session.last_will.topic  = topicos_[TOP_ESTADO];
    conf.session.last_will.msg    = "{\"en_linea\":false}";
    conf.session.last_will.msg_len = (int)strlen("{\"en_linea\":false}");
    conf.session.last_will.qos    = 1;
    conf.session.last_will.retain = 1;
    conf.buffer.size              = (int)cfg::MQTT_BUFFER;
    conf.buffer.out_size          = (int)cfg::MQTT_BUFFER;
    conf.network.reconnect_timeout_ms = 2000;          // backoff de reconexion
    conf.network.timeout_ms       = 5000;
    conf.task.priority            = 5;

    cliente_ = esp_mqtt_client_init(&conf);
    if (cliente_ == nullptr) {
      Serial.println("[MQTT] ERROR: no se pudo crear el cliente");
      return;
    }
    esp_mqtt_client_register_event(cliente_, MQTT_EVENT_ANY, &ClienteMQTT::manejadorEventos, this);
    esp_mqtt_client_start(cliente_);

    // Tarea publicadora en el NUCLEO 0 (los sensores viven en el nucleo 1).
    xTaskCreatePinnedToCore(&ClienteMQTT::trampolin, "mqtt_pub", 6144, this, 2, nullptr, 0);
  }

  const char* topico(SufijoTopico s) const { return topicos_[s]; }
  bool conectado() const { return conectado_; }

  /// Encola un evento (QoS 1, prioridad): nunca se descarta por antiguedad.
  bool publicarEvento(const char* json) { return encolar(colaEventos_, TOP_EVENTO, json, 1, false); }

  /// Encola telemetria/GPS (QoS 0): si la cola esta llena se tira lo mas viejo.
  bool publicarDato(SufijoTopico sufijo, const char* json) {
    return encolar(colaDatos_, sufijo, json, 0, false);
  }

  /// Publicacion inmediata (estado y modo son retenidos y no pueden esperar).
  bool publicarRetenido(SufijoTopico sufijo, const char* json) {
    if (cliente_ == nullptr) return false;
    const int r = esp_mqtt_client_publish(cliente_, topicos_[sufijo], json, 0, 1, 1);
    if (r >= 0) publicados_++; else fallos_++;
    return r >= 0;
  }

  bool publicarSync(const char* json) {
    if (cliente_ == nullptr || !conectado_) return false;
    const int r = esp_mqtt_client_publish(cliente_, topicos_[TOP_SYNC_REQ], json, 0, 0, 0);
    if (r >= 0) publicados_++; else fallos_++;
    return r >= 0;
  }

  uint32_t reconexiones() const { return reconexiones_; }
  uint32_t publicados()   const { return publicados_; }
  uint32_t descartados()  const { return descartados_; }
  uint32_t fallos()       const { return fallos_; }
  /// true una sola vez por conexion: loop() lo usa para el saludo inicial.
  bool consumirConexionNueva() {
    const bool nueva = conexionNueva_;
    conexionNueva_ = false;
    return nueva;
  }

private:
  static void trampolin(void* p) { static_cast<ClienteMQTT*>(p)->tareaPublicacion(); }

  bool encolar(QueueHandle_t cola, uint8_t sufijo, const char* json, uint8_t qos, bool retener) {
    if (cola == nullptr) return false;
    Paquete p;
    p.sufijo  = sufijo;
    p.qos     = qos;
    p.retener = retener;
    p.largo   = (uint16_t)strlcpy(p.json, json, sizeof(p.json));
    if (p.largo >= sizeof(p.json)) {
      Serial.println("[MQTT] AVISO: payload truncado (revisa el tamano del JSON)");
      p.largo = sizeof(p.json) - 1;
    }
    if (xQueueSend(cola, &p, 0) == pdTRUE) return true;

    // Cola llena. En QoS 0 (telemetria/GPS) el dato mas nuevo vale mas que el
    // mas viejo: se descarta el viejo y se reintenta.
    if (qos == 0) {
      Paquete viejo;
      if (xQueueReceive(cola, &viejo, 0) == pdTRUE) {
        descartados_++;
        if (xQueueSend(cola, &p, 0) == pdTRUE) return true;
      }
    }
    descartados_++;
    return false;
  }

  void tareaPublicacion() {
    Paquete p;
    for (;;) {
      if (!conectado_) { vTaskDelay(pdMS_TO_TICKS(50)); continue; }

      // Los eventos tienen prioridad absoluta sobre telemetria y GPS.
      if (xQueueReceive(colaEventos_, &p, 0) == pdTRUE) { publicar(p); continue; }
      if (xQueueReceive(colaDatos_, &p, pdMS_TO_TICKS(20)) == pdTRUE) publicar(p);
    }
  }

  void publicar(const Paquete& p) {
    const int r = esp_mqtt_client_publish(
      cliente_, topicos_[p.sufijo], p.json, (int)p.largo, p.qos, p.retener ? 1 : 0);
    if (r >= 0) publicados_++; else fallos_++;
  }

  static void manejadorEventos(void* manejador, esp_event_base_t /*base*/,
                               int32_t id, void* datos) {
    static_cast<ClienteMQTT*>(manejador)->alEvento(id, (esp_mqtt_event_handle_t)datos);
  }

  void alEvento(int32_t id, esp_mqtt_event_handle_t e) {
    switch ((esp_mqtt_event_id_t)id) {
      case MQTT_EVENT_CONNECTED:
        conectado_     = true;
        conexionNueva_ = true;
        esp_mqtt_client_subscribe(cliente_, topicoConfig_, 1);
        esp_mqtt_client_subscribe(cliente_, topicoCmd_, 1);
        esp_mqtt_client_subscribe(cliente_, topicoSyncResp_, 0);
        Serial.printf("[MQTT] Conectado a %s:%u como \"%s\"\n",
                      cfg::MQTT_HOST, cfg::MQTT_PUERTO, clientId_);
        break;

      case MQTT_EVENT_DISCONNECTED:
        if (conectado_) reconexiones_++;
        conectado_ = false;
        Serial.println("[MQTT] Desconectado; esp-mqtt reintenta solo.");
        break;

      case MQTT_EVENT_DATA: {
        if (e == nullptr || e->topic == nullptr || receptor_ == nullptr) break;
        // El sufijo relevante son los segmentos posteriores al codigo.
        char topico[96];
        const int nT = e->topic_len < (int)sizeof(topico) - 1 ? e->topic_len : (int)sizeof(topico) - 1;
        memcpy(topico, e->topic, nT);
        topico[nT] = '\0';

        char cuerpo[768];
        const int nD = e->data_len < (int)sizeof(cuerpo) - 1 ? e->data_len : (int)sizeof(cuerpo) - 1;
        memcpy(cuerpo, e->data, nD);
        cuerpo[nD] = '\0';

        receptor_(contexto_, sufijoDe(topico), cuerpo, (size_t)nD);
        break;
      }

      case MQTT_EVENT_ERROR:
        Serial.println("[MQTT] Error de transporte (revisa IP, usuario y clave del broker).");
        break;

      default:
        break;
    }
  }

  /// "ucsm/v1/NODO-01/sync/resp" -> "sync/resp"
  static const char* sufijoDe(const char* topico) {
    int barras = 0;
    for (const char* p = topico; *p; p++) {
      if (*p == '/' && ++barras == 3) return p + 1;
    }
    return topico;
  }

  esp_mqtt_client_handle_t cliente_ = nullptr;
  Receptor receptor_ = nullptr;
  void*    contexto_ = nullptr;

  char topicos_[TOP_TOTAL][96] = {};
  char topicoConfig_[96]   = {};
  char topicoCmd_[96]      = {};
  char topicoSyncResp_[96] = {};
  char clientId_[40]       = {};

  QueueHandle_t colaEventos_ = nullptr, colaDatos_ = nullptr;
  volatile bool conectado_     = false;
  volatile bool conexionNueva_ = false;
  uint32_t reconexiones_ = 0, publicados_ = 0, descartados_ = 0, fallos_ = 0;
};

// ============================================================================
//  SensorIMU  (MPU-6050 por registros: +-16 g, 500 Hz, tarea en el nucleo 1)
// ============================================================================
struct VentanaIMU {
  uint32_t n = 0, errores = 0;
  float ax = 0, ay = 0, az = 0;      // media (g)
  float aMax = 0;                    // |a| maximo (g), ahora hasta 16 g
  float vibRms = 0, vibPp = 0;       // desviacion y pico-pico de |a| (g)
  float vibMax = 0;                  // | |a| - media_movil | maximo (g)
  float variacion = 0;               // max(|a|) - min(|a|) -> MaquinaModo
  float giroMax = 0;                 // deg/s
  float temp = 0;                    // C
  float pitch = 0, roll = 0;         // deg
};

class SensorIMU {
public:
  SensorIMU(OrquestadorReglas& reglas) : reglas_(reglas) {}

  bool begin() {
    uint8_t who = 0;
    if (!leer(0x75, &who, 1)) return false;                   // WHO_AM_I
    Serial.printf("[IMU] WHO_AM_I = 0x%02X %s\n", who,
                  who == 0x68 ? "(MPU-6050 OK)" : "(compatible, se intenta igual)");
    escribir(0x6B, 0x01); delay(100);   // despertar, reloj PLL del giroscopio
    escribir(0x19, 0x01);               // SMPLRT_DIV = 1 -> 1 kHz / 2 = 500 Hz
    escribir(0x1A, 0x01);               // CONFIG: DLPF ~184 Hz (no recorta picos)
    escribir(0x1B, 0x08);               // GYRO_CONFIG:  +-500 deg/s (65.5 LSB/dps)
    escribir(0x1C, 0x18);               // ACCEL_CONFIG: +-16 g (2048 LSB/g)
    reiniciarAcc();
    ok_ = true;
    return true;
  }

  void iniciarTarea() {
    if (ok_) xTaskCreatePinnedToCore(&SensorIMU::trampolin, "imu", 4096, this, 4, nullptr, 1);
  }

  bool  ok() const { return ok_; }
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
    v.vibRms    = var > 0 ? sqrt(var) : 0;
    v.vibPp     = a.mmax - a.mmin;
    v.aMax      = a.mmax;
    v.vibMax    = a.vibmax;
    v.variacion = a.mmax - a.mmin;      // = variacion de la ventana (MaquinaModo)
    v.giroMax   = a.gmax;
    v.temp      = a.st / a.n;
    v.pitch     = atan2f(-v.ax, sqrtf(v.ay * v.ay + v.az * v.az)) * 57.29578f;
    v.roll      = atan2f(v.ay, v.az) * 57.29578f;
    return v;
  }

private:
  struct Acc {
    uint32_t n = 0, errores = 0;
    double sx = 0, sy = 0, sz = 0, sm = 0, sm2 = 0, st = 0;
    float mmin = 99, mmax = 0, gmax = 0, vibmax = 0;
  };

  static constexpr uint8_t DIR = 0x68;        // AD0 a GND
  static constexpr float   LSB_POR_G   = 2048.0f;   // +-16 g
  static constexpr float   LSB_POR_DPS = 65.5f;     // +-500 deg/s

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
    // A 500 Hz el periodo son 2 ms; con tick de 1 ms vTaskDelayUntil mantiene
    // la cadencia sin derivar.
    const TickType_t periodo = pdMS_TO_TICKS(1000 / cfg::IMU_HZ);
    for (;;) {
      vTaskDelayUntil(&ultimo, periodo > 0 ? periodo : 1);
      if (!leer(0x3B, b, 14)) {
        portENTER_CRITICAL(&mux_); acc_.errores++; portEXIT_CRITICAL(&mux_);
        continue;
      }
      const int16_t rax = (b[0] << 8) | b[1],  ray = (b[2] << 8) | b[3],  raz = (b[4] << 8) | b[5];
      const int16_t rt  = (b[6] << 8) | b[7];
      const int16_t rgx = (b[8] << 8) | b[9],  rgy = (b[10] << 8) | b[11], rgz = (b[12] << 8) | b[13];

      const float ax = rax / LSB_POR_G, ay = ray / LSB_POR_G, az = raz / LSB_POR_G;
      const float gx = rgx / LSB_POR_DPS, gy = rgy / LSB_POR_DPS, gz = rgz / LSB_POR_DPS;
      const float t  = rt / 340.0f + 36.53f;
      const float m  = sqrtf(ax * ax + ay * ay + az * az);   // |a| en g
      const float gm = sqrtf(gx * gx + gy * gy + gz * gz);   // |w| en deg/s
      ultimoMod_ = m;

      // Media movil exponencial de |a|: al restarla desaparece la gravedad y
      // queda solo la vibracion (seccion 3.4).
      mediaMod_ += 0.01f * (m - mediaMod_);
      const float vib = fabsf(m - mediaMod_);

      portENTER_CRITICAL(&mux_);
      acc_.n++;
      acc_.sx += ax; acc_.sy += ay; acc_.sz += az;
      acc_.sm += m;  acc_.sm2 += (double)m * m; acc_.st += t;
      if (m < acc_.mmin) acc_.mmin = m;
      if (m > acc_.mmax) acc_.mmax = m;
      if (gm > acc_.gmax) acc_.gmax = gm;
      if (vib > acc_.vibmax) acc_.vibmax = vib;
      portEXIT_CRITICAL(&mux_);

      // 1 s de calentamiento para que la media movil converja.
      if (++muestras < cfg::IMU_HZ) continue;
      reglas_.muestraIMU(m, vib, RelojSincronizado::monotonicoMs());
    }
  }

  OrquestadorReglas& reglas_;
  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  Acc acc_;
  bool ok_ = false;
  float mediaMod_ = 1.0f;
  volatile float ultimoMod_ = 0;
};

// ============================================================================
//  SensorAudio  (INMP441 por I2S, 24 bits en palabras de 32, canal izquierdo)
//  ---------------------------------------------------------------------------
//  Bloques de 256 muestras (16 ms) a 16 kHz. Por bloque: dB SPL de pico, FFT
//  de 256 puntos con ventana de Hann, energia de las bandas 150-400 Hz y
//  3-7 kHz y planitud espectral. Se mide el tiempo de CPU de la FFT para
//  demostrar que cabe de sobra en los 16 ms (se reporta en la telemetria).
// ============================================================================
class SensorAudio {
public:
  SensorAudio(OrquestadorReglas& reglas) : reglas_(reglas), analizador_(&fft_) {}

  bool begin() {
    const bool acelerada = fft_.begin();
    Serial.printf("[AUDIO] FFT de %u puntos: %s\n", (unsigned)audio_cfg::FFT_PUNTOS,
                  acelerada ? "esp-dsp (acelerada)" : "TransformadaRadix2 (interna)");
    i2s_.setPins(cfg::I2S_SCK_PIN, cfg::I2S_WS_PIN, -1, cfg::I2S_SD_PIN);
    ok_ = i2s_.begin(I2S_MODE_STD, cfg::AUDIO_HZ, I2S_DATA_BIT_WIDTH_32BIT,
                     I2S_SLOT_MODE_MONO, I2S_STD_SLOT_LEFT);
    return ok_;
  }

  void iniciarTarea() {
    if (ok_) xTaskCreatePinnedToCore(&SensorAudio::trampolin, "audio", 8192, this, 3, nullptr, 1);
  }

  bool     ok() const { return ok_; }
  uint32_t fftMicros() const { return fftUs_; }
  uint32_t bloques()   const { return bloques_; }

  /// Resumen de la ventana de 1 s para la telemetria.
  void cerrarVentana(float& rmsDbfs, float& splDb, uint32_t& muestras) {
    uint32_t n; double sumsq;
    portENTER_CRITICAL(&mux_);
    n = n_; sumsq = sumsq_;
    n_ = 0; sumsq_ = 0;
    portEXIT_CRITICAL(&mux_);

    rmsDbfs = -140.0f;
    if (n > 0) {
      const float rms = sqrt(sumsq / n);
      if (rms > 0) rmsDbfs = 20.0f * log10f(rms / audio_cfg::FONDO_ESCALA);
    }
    splDb = audio_cfg::SPL_OFFSET_DB + rmsDbfs;
    muestras = n;
  }

private:
  static void trampolin(void* p) { static_cast<SensorAudio*>(p)->tarea(); }

  void tarea() {
    for (;;) {
      const size_t bytes = i2s_.readBytes((char*)crudo_, sizeof(crudo_));
      const size_t n = bytes / sizeof(int32_t);
      if (n == 0) { vTaskDelay(1); continue; }

      // El INMP441 entrega 24 bits alineados a la izquierda en 32: >> 8.
      double sumsq = 0;
      for (size_t i = 0; i < n; i++) {
        muestras_[i] = (float)(crudo_[i] >> 8);
        sumsq += (double)muestras_[i] * muestras_[i];
      }

      portENTER_CRITICAL(&mux_);
      n_ += n; sumsq_ += sumsq;
      portEXIT_CRITICAL(&mux_);

      // --- Analisis espectral del bloque (FUERA de cualquier lock) ---
      const int64_t t0 = esp_timer_get_time();
      const ResultadoBloque b = analizador_.analizar(muestras_, n, RelojSincronizado::monotonicoMs());
      fftUs_ = (uint32_t)(esp_timer_get_time() - t0);

      // ~1 s de estabilizacion del microfono antes de alimentar las reglas.
      if (++bloques_ < 60) continue;
      reglas_.bloqueAudio(b);
    }
  }

  OrquestadorReglas&  reglas_;
  TransformadaEspDsp  fft_;
  AnalizadorEspectral analizador_;
  I2SClass i2s_;
  bool ok_ = false;

  int32_t crudo_[cfg::AUDIO_BLOQUE];
  float   muestras_[cfg::AUDIO_BLOQUE];

  portMUX_TYPE mux_ = portMUX_INITIALIZER_UNLOCKED;
  uint32_t n_ = 0;
  double   sumsq_ = 0;
  volatile uint32_t fftUs_ = 0;
  uint32_t bloques_ = 0;
};

// ============================================================================
//  SensorGPS  (NEO-6M configurado por UBX a 5 Hz y 38400 baudios)
//  ---------------------------------------------------------------------------
//  Por defecto el NEO-6M sale de fabrica a 1 Hz, 9600 baudios y con las 6
//  sentencias NMEA activas. Para 5 Hz hay que:
//    1) CFG-PRT  (0x06 0x00): subir el UART1 del GPS a 38400 baudios. A 9600
//       las 2 sentencias x 5 Hz entran muy justas y se pierden tramas.
//    2) CFG-MSG  (0x06 0x01): apagar GLL, GSA, GSV y VTG; dejar solo RMC (que
//       trae hora, posicion, velocidad y RUMBO) y GGA (satelites, HDOP, altitud).
//    3) CFG-RATE (0x06 0x08): measRate = 200 ms -> 5 Hz.
//  Despues de subir la velocidad se VERIFICA que lleguen tramas validas; si no,
//  se vuelve a 9600 y se reconfigura ahi (el sistema sigue funcionando, solo
//  con menos tasa efectiva).
// ============================================================================
struct LecturaGPS {
  bool fix = false;
  double lat = 0, lon = 0;
  float velKmh = 0, altM = 0, hdop = 99.9f, rumboDeg = 0;
  int sats = 0;
  uint32_t edadMs = 0;
  uint32_t caracteres = 0;
  bool rechazadaPorHdop = false;
};

class SensorGPS {
public:
  SensorGPS() : serial_(1) {}

  void begin() {
    serial_.begin(cfg::GPS_BAUD_INICIAL, SERIAL_8N1, cfg::GPS_RX_PIN, cfg::GPS_TX_PIN);
    delay(200);
    configurar();
  }

  void actualizar() { while (serial_.available()) gps_.encode(serial_.read()); }

  uint32_t baudios() const { return baudios_; }
  bool     configurado() const { return configurado_; }

  LecturaGPS lectura() {
    LecturaGPS l;
    l.edadMs = gps_.location.isValid() ? gps_.location.age() : 0xFFFFFFFF;
    l.hdop   = gps_.hdop.isValid() ? gps_.hdop.hdop() : 99.9f;
    l.sats   = gps_.satellites.isValid() ? gps_.satellites.value() : 0;
    l.caracteres = gps_.charsProcessed();

    const bool posicionValida = gps_.location.isValid() && gps_.location.age() < 3000;
    if (posicionValida && l.hdop > cfg::GPS_HDOP_MAXIMO) {
      // Posicion demasiado imprecisa: se marca como "sin fix" por HDOP para no
      // ensuciar el rastro del mapa con saltos de decenas de metros.
      l.rechazadaPorHdop = true;
      return l;
    }
    l.fix = posicionValida;
    if (!l.fix) return l;

    l.lat      = gps_.location.lat();
    l.lon      = gps_.location.lng();
    l.velKmh   = gps_.speed.isValid() ? gps_.speed.kmph() : 0;
    l.altM     = gps_.altitude.isValid() ? gps_.altitude.meters() : 0;
    l.rumboDeg = gps_.course.isValid() ? gps_.course.deg() : 0;
    return l;
  }

private:
  /// Calcula la suma de verificacion Fletcher de 8 bits de un marco UBX.
  static void checksumUbx(uint8_t* marco, size_t largo) {
    uint8_t a = 0, b = 0;
    for (size_t i = 2; i < largo - 2; i++) { a += marco[i]; b += a; }
    marco[largo - 2] = a;
    marco[largo - 1] = b;
  }

  void enviarUbx(uint8_t clase, uint8_t id, const uint8_t* carga, uint16_t largoCarga) {
    uint8_t marco[64];
    if (largoCarga + 8u > sizeof(marco)) return;
    marco[0] = 0xB5; marco[1] = 0x62;
    marco[2] = clase; marco[3] = id;
    marco[4] = (uint8_t)(largoCarga & 0xFF);
    marco[5] = (uint8_t)(largoCarga >> 8);
    for (uint16_t i = 0; i < largoCarga; i++) marco[6 + i] = carga[i];
    checksumUbx(marco, largoCarga + 8u);
    serial_.write(marco, largoCarga + 8u);
    serial_.flush();
    delay(80);
  }

  /// CFG-MSG: activa/desactiva una sentencia NMEA en el puerto UART1.
  void configurarSentencia(uint8_t idNmea, uint8_t tasa) {
    const uint8_t carga[8] = { 0xF0, idNmea, 0, tasa, 0, 0, 0, 0 };
    enviarUbx(0x06, 0x01, carga, sizeof(carga));
  }

  /// CFG-RATE: periodo de medicion en ms (200 ms = 5 Hz).
  void configurarTasa(uint16_t measRateMs) {
    const uint8_t carga[6] = {
      (uint8_t)(measRateMs & 0xFF), (uint8_t)(measRateMs >> 8),  // measRate
      0x01, 0x00,                                                // navRate = 1 ciclo
      0x01, 0x00,                                                // timeRef = GPS
    };
    enviarUbx(0x06, 0x08, carga, sizeof(carga));
  }

  /// CFG-PRT: UART1 del GPS a la velocidad indicada, NMEA de entrada y salida.
  void configurarVelocidadPuerto(uint32_t baudios) {
    const uint8_t carga[20] = {
      0x01, 0x00, 0x00, 0x00,                       // portID = 1 (UART1), reservado
      0xD0, 0x08, 0x00, 0x00,                       // mode: 8N1
      (uint8_t)(baudios & 0xFF), (uint8_t)((baudios >> 8) & 0xFF),
      (uint8_t)((baudios >> 16) & 0xFF), (uint8_t)((baudios >> 24) & 0xFF),
      0x03, 0x00,                                   // inProtoMask  = UBX + NMEA
      0x02, 0x00,                                   // outProtoMask = NMEA
      0x00, 0x00, 0x00, 0x00,
    };
    enviarUbx(0x06, 0x00, carga, sizeof(carga));
  }

  /// Envia las tres configuraciones y devuelve true si siguen llegando tramas.
  bool aplicarConfiguracion() {
    configurarSentencia(0x01, 0);   // GLL off
    configurarSentencia(0x02, 0);   // GSA off
    configurarSentencia(0x03, 0);   // GSV off
    configurarSentencia(0x05, 0);   // VTG off
    configurarSentencia(0x00, 1);   // GGA on  (satelites, HDOP, altitud)
    configurarSentencia(0x04, 1);   // RMC on  (posicion, velocidad, rumbo)
    configurarTasa(200);            // 5 Hz
    return verificarTramas();
  }

  /// Lee 1.5 s y confirma que al menos una sentencia NMEA pasa el checksum.
  bool verificarTramas() {
    const uint32_t antes = gps_.passedChecksum();
    const uint32_t t0 = millis();
    while (millis() - t0 < 1500) {
      while (serial_.available()) gps_.encode(serial_.read());
      delay(5);
    }
    return gps_.passedChecksum() > antes;
  }

  void configurar() {
    // 1) Subir el GPS a 38400 (el comando se envia todavia a 9600).
    configurarVelocidadPuerto(cfg::GPS_BAUD_RAPIDO);
    serial_.updateBaudRate(cfg::GPS_BAUD_RAPIDO);
    delay(200);

    if (aplicarConfiguracion()) {
      baudios_ = cfg::GPS_BAUD_RAPIDO;
      configurado_ = true;
      Serial.printf("[GPS] NEO-6M a 5 Hz, %lu baudios (RMC + GGA)\n", (unsigned long)baudios_);
      return;
    }

    // 2) Retroceso: el modulo no acepto 38400 (clon o firmware distinto).
    Serial.println("[GPS] AVISO: no responde a 38400; se vuelve a 9600 baudios.");
    serial_.updateBaudRate(cfg::GPS_BAUD_INICIAL);
    delay(200);
    configurado_ = aplicarConfiguracion();
    baudios_ = cfg::GPS_BAUD_INICIAL;
    Serial.printf("[GPS] NEO-6M a %s, 9600 baudios\n",
                  configurado_ ? "5 Hz" : "configuracion por defecto (1 Hz)");
  }

  TinyGPSPlus gps_;
  HardwareSerial serial_;
  uint32_t baudios_ = cfg::GPS_BAUD_INICIAL;
  bool configurado_ = false;
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
//  NodoVehicular  (orquestador)
// ============================================================================
class NodoVehicular {
public:
  NodoVehicular() : imu_(reglas_), audio_(reglas_) {}

  void setup() {
    Serial.begin(115200);
    delay(1500);
    Serial.printf("\n=== NODO IoT VEHICULAR ESP32-S3  v%s  (%s) ===\n",
                  cfg::VERSION_FIRMWARE, cfg::CODIGO_DISPOSITIVO);
    bootId_ = esp_random();

    reglas_.begin(umbralesPorDefecto());

    if (cfg::USAR_CONTACTO) {
      pinMode(cfg::PIN_CONTACTO, cfg::CONTACTO_ACTIVO_BAJO ? INPUT_PULLUP : INPUT);
      Serial.printf("[MODO] Senal de contacto habilitada en GPIO%d (activo %s)\n",
                    cfg::PIN_CONTACTO, cfg::CONTACTO_ACTIVO_BAJO ? "BAJO" : "ALTO");
    } else {
      Serial.println("[MODO] Sin senal de contacto: se usa la regla inercial del MPU.");
    }

    gps_.begin();

    Wire.begin(cfg::I2C_SDA_PIN, cfg::I2C_SCL_PIN, 400000);
    Serial.println(imu_.begin() ? "[IMU] MPU-6050: +-16 g (2048 LSB/g), +-500 dps, 500 Hz"
                                : "[IMU] ERROR: MPU-6050 no responde en 0x68");
    Serial.println(audio_.begin() ? "[AUDIO] INMP441 a 16 kHz, bloques de 256 muestras (16 ms)"
                                  : "[AUDIO] ERROR al iniciar I2S");
    bateria_.begin();

    conectarWiFi();
    reloj_.iniciarNTP();
    mqtt_.begin(&NodoVehicular::alMensajeMqtt, this);
    imu_.iniciarTarea();
    audio_.iniciarTarea();

    Serial.printf("[MQTT] %s:%u  topicos %s/%s/...\n", cfg::MQTT_HOST, cfg::MQTT_PUERTO,
                  cfg::PREFIJO_TOPICO, cfg::CODIGO_DISPOSITIVO);

    const int64_t ahora = RelojSincronizado::monotonicoMs();
    proximoReporteMs_ = ahora + cfg::PERIODO_REPORTE_MS;
    proximoGpsMs_     = ahora + cfg::GPS_PERIODO_ESTACIONADO_MS;
    proximoSyncMs_    = ahora;
    proximaVentanaMs_ = ahora + 1000;
  }

  void loop() {
    gps_.actualizar();
    const int64_t ahora = RelojSincronizado::monotonicoMs();

    if (mqtt_.consumirConexionNueva()) alConectarMqtt();

    leerContacto(ahora);
    reglas_.evaluarTemporizadores(ahora);

    EventoDetectado evento;
    while (reglas_.siguienteEvento(evento)) publicarEvento(evento);

    if (reglas_.consumirCambioDeModo()) publicarModo();

    if (ahora >= proximaVentanaMs_) {
      proximaVentanaMs_ += 1000;
      cerrarVentanaDeSegundo();
    }
    if (ahora >= proximoGpsMs_) {
      proximoGpsMs_ = ahora + periodoGpsMs();
      publicarGps();
    }
    if (ahora >= proximoReporteMs_) {
      proximoReporteMs_ += cfg::PERIODO_REPORTE_MS;
      publicarTelemetria();
    }
    if (ahora >= proximoSyncMs_) {
      proximoSyncMs_ = ahora + (syncsRapidosPendientes_ > 0 ? 300 : cfg::SYNC_PERIODO_MS);
      if (syncsRapidosPendientes_ > 0) syncsRapidosPendientes_--;
      publicarSync();
    }
    delay(2);
  }

private:
  // ---------------------------------------------------------------- Umbrales
  static UmbralesDeteccion umbralesPorDefecto() {
    UmbralesDeteccion u;
    u.choqueG             = cfg::CHOQUE_G;
    u.choqueDb            = cfg::CHOQUE_DB;
    u.choqueVentanaMs     = cfg::CHOQUE_VENTANA_MS;
    u.golpeBajaDb         = cfg::GOLPE_BAJA_DB;
    u.estallidoAltaDb     = cfg::ESTALLIDO_ALTA_DB;
    u.planitudMin         = cfg::PLANITUD_MIN;
    u.estallidoVentanaMs  = cfg::ESTALLIDO_VENTANA_MS;
    u.estallidoMinMs      = cfg::ESTALLIDO_MIN_MS;
    u.fusionMs            = cfg::FUSION_MS;
    u.vibracionG          = cfg::VIBRACION_G;
    u.golpeSinEstallidoMs = cfg::GOLPE_SIN_ESTALLIDO_MS;
    u.quietoG             = cfg::QUIETO_G;
    u.quietoS             = cfg::QUIETO_S;
    u.marchaS             = cfg::MARCHA_S;
    return u;
  }

  // ---------------------------------------------------------------- Wi-Fi
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

  // ---------------------------------------------------------------- Contacto
  void leerContacto(int64_t ahora) {
    if (!cfg::USAR_CONTACTO) return;
    const int nivel = digitalRead(cfg::PIN_CONTACTO);
    const bool encendido = cfg::CONTACTO_ACTIVO_BAJO ? (nivel == LOW) : (nivel == HIGH);
    contactoActual_ = encendido;
    reglas_.senalContacto(encendido, ahora);
  }

  // ---------------------------------------------------- MQTT: entrada
  static void alMensajeMqtt(void* contexto, const char* sufijo, const char* json, size_t largo) {
    static_cast<NodoVehicular*>(contexto)->procesarMensaje(sufijo, json, largo);
  }

  void procesarMensaje(const char* sufijo, const char* json, size_t /*largo*/) {
    JsonDocument doc;
    if (deserializeJson(doc, json)) {
      Serial.printf("[MQTT] JSON invalido en \"%s\"\n", sufijo);
      return;
    }

    if (strcmp(sufijo, "sync/resp") == 0) {
      const uint32_t id = doc["id"] | 0u;
      const int64_t t0  = doc["t0"] | (int64_t)0;
      const int64_t srv = doc["server_ts_ms"] | (int64_t)0;
      if (reloj_.procesarRespuesta(id, t0, srv))
        Serial.printf("[SYNC] rtt=%lld ms (mejor %lld ms) fuente=%s\n",
                      (long long)reloj_.ultimoRttMs(), (long long)reloj_.mejorRttMs(), reloj_.fuente());
      return;
    }

    if (strcmp(sufijo, "config") == 0) { aplicarConfig(doc); return; }
    if (strcmp(sufijo, "cmd") == 0)    { aplicarComando(doc); return; }
  }

  void aplicarConfig(JsonDocument& doc) {
    if (doc["armado"].is<bool>()) {
      reglas_.setArmado(doc["armado"].as<bool>());
      Serial.printf("[CONFIG] armado = %s\n", reglas_.armado() ? "SI" : "no");
    }

    JsonObjectConst j = doc["umbrales"];
    if (j.isNull()) return;

    UmbralesDeteccion u = reglas_.umbrales();
    u.choqueG             = j["choque_g"]               | u.choqueG;
    u.choqueDb            = j["choque_db"]              | u.choqueDb;
    u.choqueVentanaMs     = j["choque_ventana_ms"]      | u.choqueVentanaMs;
    u.golpeBajaDb         = j["golpe_baja_db"]          | u.golpeBajaDb;
    u.estallidoAltaDb     = j["estallido_alta_db"]      | u.estallidoAltaDb;
    u.planitudMin         = j["planitud_min"]           | u.planitudMin;
    u.estallidoVentanaMs  = j["estallido_ventana_ms"]   | u.estallidoVentanaMs;
    u.estallidoMinMs      = j["estallido_min_ms"]       | u.estallidoMinMs;
    u.fusionMs            = j["fusion_ms"]              | u.fusionMs;
    u.vibracionG          = j["vibracion_g"]            | u.vibracionG;
    u.golpeSinEstallidoMs = j["golpe_sin_estallido_ms"] | u.golpeSinEstallidoMs;
    u.quietoG             = j["quieto_g"]               | u.quietoG;
    u.quietoS             = j["quieto_s"]               | u.quietoS;
    u.marchaS             = j["marcha_s"]               | u.marchaS;
    reglas_.setUmbrales(u);

    Serial.printf("[CONFIG] umbrales actualizados: choque %.1f g / %.0f dB, "
                  "estallido +%.0f dB plan>=%.2f, vibracion %.2f g\n",
                  u.choqueG, u.choqueDb, u.estallidoAltaDb, u.planitudMin, u.vibracionG);
  }

  void aplicarComando(JsonDocument& doc) {
    const char* accion = doc["accion"] | "";

    if (strcmp(accion, "reiniciar") == 0) {
      Serial.println("[CMD] reiniciar: el nodo se reinicia en 300 ms.");
      publicarEstado(false);
      delay(300);
      ESP.restart();
      return;
    }
    if (strcmp(accion, "recalibrar_piso") == 0) {
      reglas_.recalibrarPiso();
      Serial.println("[CMD] recalibrar_piso: el piso de ruido se vuelve a aprender (2 s).");
      return;
    }
    if (strcmp(accion, "forzar_modo") == 0) {
      const char* texto = doc["modo"] | "";
      const Modo nuevo = strcmp(texto, "ESTACIONADO") == 0 ? Modo::ESTACIONADO : Modo::EN_MARCHA;
      reglas_.forzarModo(nuevo);
      Serial.printf("[CMD] forzar_modo: %s\n", textoModo(nuevo));
      publicarModo();
      return;
    }
    Serial.printf("[CMD] accion desconocida: \"%s\"\n", accion);
  }

  void alConectarMqtt() {
    publicarEstado(true);
    publicarModo();
    // 5 sincronizaciones seguidas al conectar para fijar rapido el offset.
    syncsRapidosPendientes_ = cfg::SYNC_AL_CONECTAR;
    proximoSyncMs_ = RelojSincronizado::monotonicoMs();
  }

  // ---------------------------------------------------- MQTT: salida
  void publicarEstado(bool enLinea) {
    JsonDocument doc;
    doc["codigo"]   = cfg::CODIGO_DISPOSITIVO;
    doc["en_linea"] = enLinea;
    doc["firmware"] = cfg::VERSION_FIRMWARE;
    doc["ip"]       = WiFi.localIP().toString();
    doc["mac"]      = WiFi.macAddress();
    doc["rssi_dbm"] = rssi();
    doc["boot_id"]  = bootId_;
    doc["armado"]   = reglas_.armado();
    doc["modo"]     = textoModo(reglas_.modo());
    char json[384];
    serializeJson(doc, json, sizeof(json));
    mqtt_.publicarRetenido(TOP_ESTADO, json);
  }

  void publicarModo() {
    JsonDocument doc;
    doc["modo"]   = textoModo(reglas_.modo());
    doc["fuente"] = textoFuenteModo(reglas_.fuenteModo());
    const int64_t ts = reloj_.ahoraMs();
    if (ts > 0) doc["ts_ms"] = ts; else doc["ts_ms"] = nullptr;
    if (cfg::USAR_CONTACTO) doc["contacto"] = contactoActual_; else doc["contacto"] = nullptr;
    char json[192];
    serializeJson(doc, json, sizeof(json));
    mqtt_.publicarRetenido(TOP_MODO, json);
    Serial.printf("[MODO] %s (fuente %s)\n",
                  textoModo(reglas_.modo()), textoFuenteModo(reglas_.fuenteModo()));
  }

  void publicarSync() {
    int64_t t0 = 0;
    const uint32_t id = reloj_.nuevaPeticion(t0);
    char json[64];
    snprintf(json, sizeof(json), "{\"id\":%lu,\"t0\":%lld}", (unsigned long)id, (long long)t0);
    mqtt_.publicarSync(json);
  }

  uint32_t periodoGpsMs() {
    return reglas_.modo() == Modo::EN_MARCHA ? cfg::GPS_PERIODO_MARCHA_MS
                                             : cfg::GPS_PERIODO_ESTACIONADO_MS;
  }

  /**
   * Latitud/longitud con 7 decimales EXACTOS. ArduinoJson no garantiza esa
   * precision al serializar un double, asi que se formatean a mano y se
   * insertan crudas con serialized(). Los buffers son MIEMBROS a proposito:
   * serialized(const char*) no copia la cadena, solo guarda el puntero, y debe
   * seguir viva hasta que serializeJson() termine.
   */
  void escribirCoordenadas(JsonObject o, double lat, double lon) {
    snprintf(bufLat_, sizeof(bufLat_), "%.7f", lat);
    snprintf(bufLon_, sizeof(bufLon_), "%.7f", lon);
    o["lat"] = serialized((const char*)bufLat_);
    o["lon"] = serialized((const char*)bufLon_);
  }

  void publicarGps() {
    const LecturaGPS g = gps_.lectura();
    JsonDocument doc;
    JsonObject o = doc.to<JsonObject>();

    const int64_t ts = reloj_.ahoraMs();
    if (ts > 0) o["ts_ms"] = ts; else o["ts_ms"] = nullptr;

    if (!g.fix) {
      o["fix"]  = false;
      o["sats"] = g.sats;
      if (g.rechazadaPorHdop) {
        o["motivo"] = "hdop";
        o["hdop"]   = r2(g.hdop);
      }
      o["modo"] = textoModo(reglas_.modo());
      char json[192];
      serializeJson(doc, json, sizeof(json));
      mqtt_.publicarDato(TOP_GPS, json);
      return;
    }

    o["fix"] = true;
    escribirCoordenadas(o, g.lat, g.lon);
    o["alt_m"]     = r2(g.altM);
    o["vel_kmh"]   = r2(g.velKmh);
    o["rumbo_deg"] = r2(g.rumboDeg);
    o["sats"]      = g.sats;
    o["hdop"]      = r2(g.hdop);
    o["edad_ms"]   = g.edadMs;
    o["modo"]      = textoModo(reglas_.modo());

    char json[256];
    serializeJson(doc, json, sizeof(json));
    mqtt_.publicarDato(TOP_GPS, json);
  }

  /// Bloque "gps" reutilizable dentro de telemetria y eventos.
  void jsonGps(JsonObject o, const LecturaGPS& g) {
    o["fix"]  = g.fix;
    o["sats"] = g.sats;
    if (!g.fix) {
      if (g.rechazadaPorHdop) o["motivo"] = "hdop";
      return;
    }
    escribirCoordenadas(o, g.lat, g.lon);
    o["vel_kmh"]   = r2(g.velKmh);
    o["rumbo_deg"] = r2(g.rumboDeg);
    o["alt_m"]     = r2(g.altM);
    o["hdop"]      = r2(g.hdop);
  }

  void jsonBateria(JsonDocument& doc, float v) {
    if (!cfg::USAR_BATERIA) { doc["bateria"] = nullptr; return; }
    JsonObject b = doc["bateria"].to<JsonObject>();
    b["v"] = r2(v);
    b["pct"] = MonitorBateria::porcentaje(v);
  }

  // ---------------------------------------------------- Evento -> .../evento
  void publicarEvento(const EventoDetectado& e) {
    const LecturaGPS g = gps_.lectura();
    JsonDocument doc;
    doc["codigo"] = cfg::CODIGO_DISPOSITIVO;
    char uid[32];
    snprintf(uid, sizeof(uid), "%08lX-%lu", (unsigned long)bootId_, (unsigned long)++numEvento_);
    doc["evento_uid"] = uid;
    doc["tipo"]  = e.tipo;
    doc["nivel"] = e.nivel;

    const int64_t ts = reloj_.aEpocaMs(e.tsMonoMs);
    if (ts > 0) doc["ts_ms"] = ts; else doc["ts_ms"] = nullptr;
    doc["ts_fuente"] = reloj_.fuente();
    doc["modo"]   = textoModo(e.modo);
    doc["armado"] = e.armado;

    JsonObject ev = doc["evidencia"].to<JsonObject>();
    if (e.esChoque) {
      ev["a_pico_g"]    = r2(e.evChoque.aPicoG);
      ev["spl_pico_db"] = r2(e.evChoque.splPicoDb);
      if (e.evChoque.hayDelta) ev["delta_ms"] = e.evChoque.deltaMs;
      else                     ev["delta_ms"] = nullptr;
    } else {
      ev["e_baja_db"]    = r2(e.evRotura.eBajaDb);
      ev["piso_baja_db"] = r2(e.evRotura.pisoBajaDb);
      ev["e_alta_db"]    = r2(e.evRotura.eAltaDb);
      ev["piso_alta_db"] = r2(e.evRotura.pisoAltaDb);
      ev["planitud"]     = r2(e.evRotura.planitud);
      ev["duracion_ms"]  = e.evRotura.duracionMs;
      ev["vib_g"]        = r2(e.evRotura.vibG);
      if (e.evRotura.hayVib) ev["delta_vib_ms"] = e.evRotura.deltaVibMs;
      else                   ev["delta_vib_ms"] = nullptr;
    }

    doc["aceleracion_g"]  = r2(e.aceleracionG);
    doc["nivel_audio_db"] = r2(e.nivelAudioDb);
    jsonGps(doc["gps"].to<JsonObject>(), g);

    char json[768];
    serializeJson(doc, json, sizeof(json));
    const bool ok = mqtt_.publicarEvento(json);
    Serial.printf(">>> EVENTO %s [%s] uid=%s  %s\n", e.tipo, e.nivel, uid,
                  ok ? "-> encolado QoS 1" : "-> COLA LLENA");
  }

  // ------------------------------------- Ventana de 1 s (modo y reglas legado)
  void cerrarVentanaDeSegundo() {
    const int64_t ahora = RelojSincronizado::monotonicoMs();
    ultimaVentana_ = imu_.ok() ? imu_.cerrarVentana() : VentanaIMU();
    if (ultimaVentana_.n > 0) reglas_.ventanaIMU(ultimaVentana_.variacion, ahora);
    if (cfg::REGLAS_LEGADO) detectarLegado(ultimaVentana_);
  }

  /**
   * Reglas de la v2.0.0 (MANIPULACION por inclinacion y MOVIMIENTO por GPS).
   * Quedan desactivadas por cfg::REGLAS_LEGADO = false para no competir con las
   * reglas definitivas; se conservan porque el catalogo tipo_evento las sigue
   * teniendo y sirven de comparacion en el capitulo de resultados.
   */
  void detectarLegado(const VentanaIMU& im) {
    const int64_t ahora = RelojSincronizado::monotonicoMs();
    if (im.n > 0) {
      if (!baseValida_ || !reglas_.armado()) {
        basePitch_ = im.pitch; baseRoll_ = im.roll;
        baseValida_ = seq_ > 3;
      } else {
        const float d = fmaxf(fabsf(im.pitch - basePitch_), fabsf(im.roll - baseRoll_));
        if (d > cfg::LEGADO_INCLINACION_DEG &&
            ahora - ultimoLegadoMs_ > (int64_t)cfg::LEGADO_COOLDOWN_MS) {
          Serial.printf("[LEGADO] MANIPULACION por inclinacion: %.1f deg\n", d);
          ultimoLegadoMs_ = ahora;
          basePitch_ = im.pitch; baseRoll_ = im.roll;
        }
      }
    }

    const LecturaGPS g = gps_.lectura();
    if (g.fix && g.velKmh > cfg::LEGADO_MOVIMIENTO_KMH) {
      if (++segundosEnMovimiento_ >= 3 &&
          ahora - ultimoLegadoMs_ > (int64_t)cfg::LEGADO_COOLDOWN_MS) {
        Serial.printf("[LEGADO] MOVIMIENTO por GPS: %.1f km/h\n", g.velKmh);
        ultimoLegadoMs_ = ahora;
      }
    } else {
      segundosEnMovimiento_ = 0;
    }
  }

  // ------------------------------------------- Telemetria -> .../telemetria
  void publicarTelemetria() {
    seq_++;
    const VentanaIMU im = ultimaVentana_;
    float rmsDbfs = -140, splDb = 0;
    uint32_t muestrasAudio = 0;
    if (audio_.ok()) audio_.cerrarVentana(rmsDbfs, splDb, muestrasAudio);
    const LecturaGPS g = gps_.lectura();
    ultimoVoltaje_ = bateria_.voltaje();

    JsonDocument doc;
    doc["codigo"]   = cfg::CODIGO_DISPOSITIVO;
    doc["firmware"] = cfg::VERSION_FIRMWARE;
    doc["mac"]      = WiFi.macAddress();
    doc["ip"]       = WiFi.localIP().toString();
    doc["boot_id"]  = bootId_;
    doc["seq"]      = seq_;
    doc["uptime_ms"] = (int64_t)RelojSincronizado::monotonicoMs();
    const int64_t ts = reloj_.ahoraMs();
    if (ts > 0) doc["ts_ms"] = ts; else doc["ts_ms"] = nullptr;
    doc["ts_fuente"] = reloj_.fuente();
    doc["armado"]    = reglas_.armado();

    // --- Campos nuevos de la v3.0.0 (seccion 3.6) ---
    doc["modo"]        = textoModo(reglas_.modo());
    doc["modo_fuente"] = textoFuenteModo(reglas_.fuenteModo());
    if (cfg::USAR_CONTACTO) doc["contacto"] = contactoActual_; else doc["contacto"] = nullptr;

    doc["wifi"]["rssi_dbm"] = rssi();
    JsonObject sis = doc["sistema"].to<JsonObject>();
    sis["ram_libre_kb"] = ESP.getFreeHeap() / 1024;
    sis["temp_c"] = imu_.ok() && im.n > 0 ? r2(im.temp) : r2(temperatureRead());

    jsonGps(doc["gps"].to<JsonObject>(), g);

    if (imu_.ok() && im.n > 0) {
      JsonObject o = doc["imu"].to<JsonObject>();
      o["ax"] = r4(im.ax); o["ay"] = r4(im.ay); o["az"] = r4(im.az);
      o["a_max_g"]      = r4(im.aMax);        // hasta 16 g
      o["vib_max_g"]    = r4(im.vibMax);
      o["variacion_g"]  = r4(im.variacion);
      o["vib_rms_g"]    = r4(im.vibRms);
      o["vib_pp_g"]     = r4(im.vibPp);
      o["giro_max_dps"] = r2(im.giroMax);
      o["pitch_deg"]    = r2(im.pitch);
      o["roll_deg"]     = r2(im.roll);
      o["muestras"]     = im.n;
    } else {
      doc["imu"] = nullptr;
    }

    if (audio_.ok() && muestrasAudio > 0) {
      JsonObject o = doc["audio"].to<JsonObject>();
      o["rms_dbfs"]     = r2(rmsDbfs);
      o["spl_db"]       = r2(splDb);
      o["spl_pico_db"]  = r2(reglas_.cerrarSplPico());
      o["piso_baja_db"] = r2(reglas_.pisoBajaDb());
      o["piso_alta_db"] = r2(reglas_.pisoAltaDb());
      o["fft_us"]       = audio_.fftMicros();
      o["muestras"]     = muestrasAudio;
    } else {
      doc["audio"] = nullptr;
    }

    jsonBateria(doc, ultimoVoltaje_);

    JsonObject m = doc["mqtt"].to<JsonObject>();
    m["reconexiones"] = mqtt_.reconexiones();
    m["publicados"]   = mqtt_.publicados();
    m["descartados"]  = mqtt_.descartados() + reglas_.eventosDescartados();
    m["rtt_sync_ms"]  = (int32_t)reloj_.mejorRttMs();

    // El contrato limita el payload a 2048 bytes (buffer del cliente MQTT);
    // el tamano debe coincidir con Paquete::json para que no se trunque al encolar.
    char json[1400];
    const size_t largo = serializeJson(doc, json, sizeof(json));
    if (largo >= sizeof(json) - 1) Serial.println("[JSON] AVISO: telemetria truncada");
    mqtt_.publicarDato(TOP_TELEMETRIA, json);

    imprimir(im, splDb, g, largo);
  }

  // ---------------------------------------------------------------- Serie
  void imprimir(const VentanaIMU& im, float splDb, const LecturaGPS& g, size_t largoJson) {
    Serial.printf("\n===== #%lu | %s | %s (%s) | reloj=%s rtt_sync=%lld ms | WiFi %d dBm =====\n",
                  (unsigned long)seq_, reglas_.armado() ? "ARMADO" : "desarmado",
                  textoModo(reglas_.modo()), textoFuenteModo(reglas_.fuenteModo()),
                  reloj_.fuente(), (long long)reloj_.mejorRttMs(), rssi());
    if (g.fix)
      Serial.printf("GPS   : FIX lat=%.7f lon=%.7f vel=%.1f km/h rumbo=%.0f sats=%d hdop=%.1f (%lu bd)\n",
                    g.lat, g.lon, g.velKmh, g.rumboDeg, g.sats, g.hdop, (unsigned long)gps_.baudios());
    else
      Serial.printf("GPS   : sin fix (sats=%d, chars=%lu)%s%s\n", g.sats, (unsigned long)g.caracteres,
                    g.rechazadaPorHdop ? "  <-- descartada por HDOP" : "",
                    g.caracteres < 10 && millis() > 5000 ? "  <-- revisa TX/RX del GPS" : "");
    if (imu_.ok())
      Serial.printf("IMU   : |a|max=%.3f g vib_max=%.4f g variacion=%.4f g giro=%.1f dps "
                    "pitch=%.1f roll=%.1f T=%.1f C (%lu)\n",
                    im.aMax, im.vibMax, im.variacion, im.giroMax, im.pitch, im.roll, im.temp,
                    (unsigned long)im.n);
    else
      Serial.println("IMU   : MPU-6050 no detectado (SDA=8, SCL=9, VCC, GND)");
    if (audio_.ok())
      Serial.printf("AUDIO : %.1f dB SPL | piso baja=%.1f dB alta=%.1f dB %s| FFT %lu us / 16000 us\n",
                    splDb, reglas_.pisoBajaDb(), reglas_.pisoAltaDb(),
                    reglas_.pisoListo() ? "" : "(aprendiendo) ",
                    (unsigned long)audio_.fftMicros());
    if (cfg::USAR_BATERIA)
      Serial.printf("BAT   : %.2f V (%d %%)\n", ultimoVoltaje_, MonitorBateria::porcentaje(ultimoVoltaje_));
    Serial.printf("MQTT  : %s | publicados=%lu reconexiones=%lu descartados=%lu fallos=%lu | json=%u B\n",
                  mqtt_.conectado() ? "conectado" : "SIN CONEXION",
                  (unsigned long)mqtt_.publicados(), (unsigned long)mqtt_.reconexiones(),
                  (unsigned long)mqtt_.descartados(), (unsigned long)mqtt_.fallos(),
                  (unsigned)largoJson);
  }

  RelojSincronizado reloj_;
  OrquestadorReglas reglas_;
  SensorIMU         imu_;
  SensorAudio       audio_;
  SensorGPS         gps_;
  MonitorBateria    bateria_;
  ClienteMQTT       mqtt_;

  uint32_t bootId_ = 0, seq_ = 0, numEvento_ = 0;
  int64_t  proximoReporteMs_ = 0, proximoGpsMs_ = 0, proximoSyncMs_ = 0, proximaVentanaMs_ = 0;
  uint8_t  syncsRapidosPendientes_ = 0;
  VentanaIMU ultimaVentana_;
  float    ultimoVoltaje_ = 0;
  bool     contactoActual_ = true;
  char     bufLat_[20] = {0}, bufLon_[20] = {0};   // ver escribirCoordenadas()

  // Estado de las reglas legado (solo si cfg::REGLAS_LEGADO = true)
  bool    baseValida_ = false;
  float   basePitch_ = 0, baseRoll_ = 0;
  uint8_t segundosEnMovimiento_ = 0;
  int64_t ultimoLegadoMs_ = 0;
};

// ============================================================================
//  PUNTO DE ENTRADA
// ============================================================================
NodoVehicular nodo;

void setup() { nodo.setup(); }
void loop()  { nodo.loop(); }
