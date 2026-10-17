/*
 * ============================================================================
 *  REGLAS DE DETECCION  (pestana del sketch nodo_iot_esp32s3)        v3.0.0
 *  Tesis: Sistema inteligente IoT con YOLO para monitoreo vehicular (UCSM)
 * ============================================================================
 *  Este encabezado contiene la LOGICA PURA de las reglas definitivas de la
 *  seccion 3.4 del contrato: NO incluye Arduino.h, ni WiFi, ni FreeRTOS, ni
 *  ningun registro de hardware. Solo recibe muestras/bloques con su marca de
 *  tiempo y devuelve decisiones.
 *
 *  Gracias a eso se compila tal cual en la PC y se prueba sin el ESP32-S3:
 *      firmware\pruebas\compilar_pruebas.bat   ->  prueba_reglas.exe
 *
 *  Clases (todas con estado propio, sin variables globales):
 *      UmbralesDeteccion   los 14 umbrales que el servidor publica en "config"
 *      TransformadaRadix2  FFT compleja en el sitio (respaldo portatil)
 *      AnalizadorEspectral FFT de 256 puntos + Hann -> SPL, energia por banda
 *                          y planitud espectral
 *      EstimadorPiso       piso de ruido autoaprendido de UNA banda
 *      ReglaChoque         Regla 1: CHOQUE / IMPACTO_SIN_CONFIRMAR
 *      ReglaRotura         Regla 2: ROTURA_VENTANA / VIDRIO_ROTO_EXTERNO /
 *                          GOLPE_MANIPULACION
 *      MaquinaModo         EN_MARCHA <-> ESTACIONADO (contacto o inercial)
 *
 *  CONVENCION DE UNIDADES (importante al llamar desde el .ino):
 *    - Aceleracion: modulo |a| en g (del MPU-6050 a +-16 g, 2048 LSB/g).
 *    - Vibracion:   | |a| - media_movil(|a|) | en g (la media movil quita la
 *                   gravedad, por eso en reposo la vibracion es ~0).
 *    - Audio:       muestras en CUENTAS de 24 bits con signo (lo que entrega
 *                   el INMP441: buf[i] >> 8), fondo de escala 2^23.
 *                   dBFS_pico = 20*log10(pico / 2^23);  dB SPL = 120 + dBFS.
 *    - Tiempos:     int64_t en milisegundos del reloj sincronizado con el
 *                   servidor (RelojSincronizado::ahoraMs()). Si no hay reloj
 *                   del servidor, sirve igual cualquier reloj monotono en ms:
 *                   las reglas solo usan diferencias.
 * ============================================================================
 */
#ifndef REGLAS_DETECCION_H
#define REGLAS_DETECCION_H

#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <string.h>

// M_PI no es estandar en C++ (g++ con -std=c++17 estricto no lo define) y en
// Arduino PI es una macro; se usa una constante propia para que el mismo
// archivo compile igual en el ESP32-S3 y en la PC.
constexpr float PI_REGLAS = 3.14159265358979323846f;

// ============================================================================
//  Constantes del analisis de audio (seccion 3.4)
// ============================================================================
namespace audio_cfg {
  constexpr float  FONDO_ESCALA   = 8388608.0f;  // 2^23 (INMP441, 24 bits)
  constexpr float  SPL_OFFSET_DB  = 120.0f;      // dB SPL = 120 + dBFS_pico
  constexpr size_t FFT_PUNTOS     = 256;         // bloque de 16 ms a 16 kHz
  constexpr uint32_t MUESTREO_HZ  = 16000;
  constexpr uint32_t BLOQUE_MS    = 16;          // 256 / 16000 = 16 ms

  // Resolucion = 16000 / 256 = 62.5 Hz por bin.
  constexpr float RESOLUCION_HZ   = 62.5f;
  constexpr size_t BIN_BAJA_INI   = 3;    // 187.5 Hz  (banda 150-400 Hz)
  constexpr size_t BIN_BAJA_FIN   = 6;    // 375.0 Hz
  constexpr size_t BIN_ALTA_INI   = 48;   // 3000 Hz   (banda 3-7 kHz)
  constexpr size_t BIN_ALTA_FIN   = 112;  // 7000 Hz

  constexpr float EPSILON         = 1e-20f;  // evita log10(0)
}

// ============================================================================
//  UmbralesDeteccion  (seccion 3.7; el servidor los publica en .../config)
// ============================================================================
struct UmbralesDeteccion {
  // --- Regla 1: CHOQUE ---
  float    choqueG            = 4.0f;    // |a| minimo del impacto (g)
  float    choqueDb           = 112.0f;  // dB SPL minimo del golpe sonoro
  uint32_t choqueVentanaMs    = 500;     // |t_a - t_s| maximo

  // --- Regla 2: ROTURA DE VENTANA ---
  float    golpeBajaDb        = 15.0f;   // dB sobre el piso de 150-400 Hz
  float    estallidoAltaDb    = 12.0f;   // dB sobre el piso de 3-7 kHz
  float    planitudMin        = 0.3f;    // planitud espectral minima (0..1)
  uint32_t estallidoVentanaMs = 300;     // golpe -> estallido
  uint32_t estallidoMinMs     = 150;     // duracion sostenida del estallido
  uint32_t fusionMs           = 500;     // |t_golpe - t_vibracion| maximo
  float    vibracionG         = 0.15f;   // vibracion que cuenta como golpe
  uint32_t golpeSinEstallidoMs = 1000;   // vibracion sola -> GOLPE_MANIPULACION

  // --- MaquinaModo (regla inercial) ---
  float    quietoG            = 0.05f;   // variacion de |a| por ventana de 1 s
  uint32_t quietoS            = 300;     // segundos quieto -> ESTACIONADO
  uint32_t marchaS            = 10;      // segundos en movimiento -> EN_MARCHA
};

// ============================================================================
//  Modo del vehiculo
// ============================================================================
enum class Modo : uint8_t { EN_MARCHA = 0, ESTACIONADO = 1 };
enum class FuenteModo : uint8_t { INERCIAL = 0, CONTACTO = 1, FORZADO = 2 };

inline const char* textoModo(Modo m) {
  return m == Modo::EN_MARCHA ? "EN_MARCHA" : "ESTACIONADO";
}
inline const char* textoFuenteModo(FuenteModo f) {
  switch (f) {
    case FuenteModo::CONTACTO: return "CONTACTO";
    case FuenteModo::FORZADO:  return "FORZADO";
    default:                   return "INERCIAL";
  }
}

// ============================================================================
//  TransformadaBase / TransformadaRadix2
//  ---------------------------------------------------------------------------
//  El analizador no sabe COMO se calcula la FFT: recibe una implementacion.
//  En la PC y como respaldo en el ESP32 se usa TransformadaRadix2 (Cooley-Tukey
//  iterativo, sin dependencias). En el ESP32-S3 el .ino puede inyectar una
//  implementacion basada en esp-dsp (dsps_fft2r_fc32), que es mas rapida.
// ============================================================================
class TransformadaBase {
public:
  virtual ~TransformadaBase() {}
  /// FFT compleja EN EL SITIO de n puntos (n potencia de 2). Sin normalizar.
  virtual void transformar(float* re, float* im, size_t n) = 0;
};

class TransformadaRadix2 : public TransformadaBase {
public:
  void transformar(float* re, float* im, size_t n) override {
    if (n < 2) return;
    // --- 1) Reordenamiento por inversion de bits ---
    for (size_t i = 1, j = 0; i < n; i++) {
      size_t bit = n >> 1;
      for (; j & bit; bit >>= 1) j ^= bit;
      j ^= bit;
      if (i < j) { intercambiar(re[i], re[j]); intercambiar(im[i], im[j]); }
    }
    // --- 2) Mariposas por etapas (longitud 2, 4, 8, ... n) ---
    for (size_t longitud = 2; longitud <= n; longitud <<= 1) {
      const float angulo = -2.0f * PI_REGLAS / (float)longitud;
      const float wRe = cosf(angulo), wIm = sinf(angulo);
      for (size_t i = 0; i < n; i += longitud) {
        float curRe = 1.0f, curIm = 0.0f;
        for (size_t k = 0; k < longitud / 2; k++) {
          const size_t a = i + k, b = i + k + longitud / 2;
          const float tRe = re[b] * curRe - im[b] * curIm;
          const float tIm = re[b] * curIm + im[b] * curRe;
          re[b] = re[a] - tRe;  im[b] = im[a] - tIm;
          re[a] = re[a] + tRe;  im[a] = im[a] + tIm;
          const float nuevoRe = curRe * wRe - curIm * wIm;
          curIm = curRe * wIm + curIm * wRe;
          curRe = nuevoRe;
        }
      }
    }
  }

private:
  static void intercambiar(float& a, float& b) { const float t = a; a = b; b = t; }
};

// ============================================================================
//  AnalizadorEspectral  (seccion 3.4: FFT de 256 puntos con ventana de Hann)
// ============================================================================
struct ResultadoBloque {
  bool     valido      = false;
  int64_t  tsMs        = 0;
  float    dbfsPico    = -140.0f;  // 20*log10(pico / 2^23)
  float    splDb       = 0.0f;     // 120 + dbfsPico
  float    eBajaDb     = -140.0f;  // energia 150-400 Hz (dB)
  float    eAltaDb     = -140.0f;  // energia 3-7 kHz (dB)
  float    planitud    = 0.0f;     // planitud espectral de la banda alta (0..1)
};

class AnalizadorEspectral {
public:
  /// @param transformada  implementacion de FFT; si es nullptr usa la interna.
  explicit AnalizadorEspectral(TransformadaBase* transformada = nullptr)
    : transformada_(transformada ? transformada : &interna_) {}

  void usarTransformada(TransformadaBase* t) { transformada_ = t ? t : &interna_; }

  /**
   * Analiza un bloque de muestras de audio en cuentas de 24 bits con signo.
   * @param muestras  puntero a n muestras (n <= 256, potencia de 2)
   * @param n         cantidad de muestras del bloque
   * @param tsMs      instante del bloque (ms del reloj sincronizado)
   */
  ResultadoBloque analizar(const float* muestras, size_t n, int64_t tsMs) {
    ResultadoBloque r;
    r.tsMs = tsMs;
    if (muestras == nullptr || n < 8 || n > audio_cfg::FFT_PUNTOS) return r;

    prepararVentana(n);

    // --- 1) Media del bloque (quita el nivel de continua del I2S) ---
    double media = 0;
    for (size_t i = 0; i < n; i++) media += muestras[i];
    media /= (double)n;

    // --- 2) Pico absoluto -> dBFS y dB SPL ---
    float pico = 0;
    for (size_t i = 0; i < n; i++) {
      const float v = fabsf(muestras[i] - (float)media);
      if (v > pico) pico = v;
    }
    r.dbfsPico = pico > 0 ? 20.0f * log10f(pico / audio_cfg::FONDO_ESCALA) : -140.0f;
    r.splDb    = audio_cfg::SPL_OFFSET_DB + r.dbfsPico;

    // --- 3) Ventana de Hann sobre la senal normalizada a fondo de escala ---
    for (size_t i = 0; i < n; i++) {
      re_[i] = (float)((muestras[i] - media) / audio_cfg::FONDO_ESCALA) * ventana_[i];
      im_[i] = 0.0f;
    }

    // --- 4) FFT y potencia por bin (normalizada por n) ---
    transformada_->transformar(re_, im_, n);
    const float escala = 1.0f / (float)n;
    const size_t bins = n / 2;
    for (size_t k = 0; k < bins; k++) {
      const float mr = re_[k] * escala, mi = im_[k] * escala;
      potencia_[k] = mr * mr + mi * mi;
    }

    // --- 5) Energia de banda en dB = 10*log10(suma|X[k]|^2 + eps) ---
    r.eBajaDb = energiaBandaDb(audio_cfg::BIN_BAJA_INI, audio_cfg::BIN_BAJA_FIN, bins);
    r.eAltaDb = energiaBandaDb(audio_cfg::BIN_ALTA_INI, audio_cfg::BIN_ALTA_FIN, bins);

    // --- 6) Planitud espectral de la banda alta (media geometrica/aritmetica)
    r.planitud = planitudBanda(audio_cfg::BIN_ALTA_INI, audio_cfg::BIN_ALTA_FIN, bins);

    r.valido = true;
    return r;
  }

private:
  void prepararVentana(size_t n) {
    if (n == ventanaN_) return;
    for (size_t i = 0; i < n; i++)
      ventana_[i] = 0.5f * (1.0f - cosf(2.0f * PI_REGLAS * (float)i / (float)(n - 1)));
    ventanaN_ = n;
  }

  float energiaBandaDb(size_t ini, size_t fin, size_t bins) const {
    if (ini >= bins) return -140.0f;
    if (fin >= bins) fin = bins - 1;
    double suma = 0;
    for (size_t k = ini; k <= fin; k++) suma += potencia_[k];
    return 10.0f * log10f((float)suma + audio_cfg::EPSILON);
  }

  /// Media geometrica / media aritmetica de |X[k]|^2 en la banda (0 = tono
  /// puro, ~0.56 = ruido blanco). Permite distinguir un estallido de vidrio
  /// (ruido de banda ancha) de una bocina o un silbato (tono).
  float planitudBanda(size_t ini, size_t fin, size_t bins) const {
    if (ini >= bins) return 0.0f;
    if (fin >= bins) fin = bins - 1;
    const size_t cuenta = fin - ini + 1;
    if (cuenta == 0) return 0.0f;

    double sumaLog = 0, sumaAritmetica = 0;
    for (size_t k = ini; k <= fin; k++) {
      const double p = (double)potencia_[k] + audio_cfg::EPSILON;
      sumaLog += log(p);
      sumaAritmetica += p;
    }
    const double geometrica = exp(sumaLog / (double)cuenta);
    const double aritmetica = sumaAritmetica / (double)cuenta;
    if (aritmetica <= 0) return 0.0f;
    const double planitud = geometrica / aritmetica;
    return planitud > 1.0 ? 1.0f : (float)planitud;
  }

  TransformadaRadix2 interna_;
  TransformadaBase*  transformada_;
  float  re_[audio_cfg::FFT_PUNTOS]       = {0};
  float  im_[audio_cfg::FFT_PUNTOS]       = {0};
  float  potencia_[audio_cfg::FFT_PUNTOS / 2] = {0};
  float  ventana_[audio_cfg::FFT_PUNTOS]  = {0};
  size_t ventanaN_ = 0;
};

// ============================================================================
//  EstimadorPiso  (seccion 3.4: piso de ruido autoaprendido, uno por banda)
//  ---------------------------------------------------------------------------
//  1) Inicializacion: promedia los primeros 2 s de bloques.
//  2) Despues: media movil exponencial con constante de tiempo ~10 s, pero
//     SOLO si el bloque esta por debajo de (piso + MARGEN_APRENDIZAJE_DB) y no
//     hay una deteccion en curso. Asi el piso sigue el ruido ambiente del
//     estacionamiento sin "aprenderse" el estallido que queremos detectar.
// ============================================================================
class EstimadorPiso {
public:
  static constexpr float    MARGEN_APRENDIZAJE_DB = 6.0f;   // piso + 6 dB
  static constexpr uint32_t INICIALIZACION_MS     = 2000;   // 2 s
  static constexpr float    TAU_S                 = 10.0f;  // ~10 s

  explicit EstimadorPiso(uint32_t bloqueMs = audio_cfg::BLOQUE_MS)
    : bloqueMs_(bloqueMs ? bloqueMs : 1) {
    alfa_ = (float)bloqueMs_ / (TAU_S * 1000.0f);
    if (alfa_ > 1.0f) alfa_ = 1.0f;
  }

  /// Vuelve a aprender el piso desde cero (comando MQTT "recalibrar_piso").
  void recalibrar() {
    listo_ = false;
    bloquesIniciales_ = 0;
    sumaInicial_ = 0;
    valorDb_ = 0;
  }

  /// @param energiaDb         energia de la banda en este bloque
  /// @param deteccionEnCurso  true mientras una regla esta evaluando un evento
  void actualizar(float energiaDb, bool deteccionEnCurso) {
    const uint32_t bloquesNecesarios = INICIALIZACION_MS / bloqueMs_;

    if (!listo_) {
      sumaInicial_ += (double)energiaDb;
      if (++bloquesIniciales_ >= bloquesNecesarios) {
        valorDb_ = (float)(sumaInicial_ / (double)bloquesIniciales_);
        listo_ = true;
      } else {
        valorDb_ = (float)(sumaInicial_ / (double)bloquesIniciales_);  // estimacion provisional
      }
      return;
    }

    if (deteccionEnCurso) return;
    if (energiaDb >= valorDb_ + MARGEN_APRENDIZAJE_DB) return;  // es un evento, no ruido de fondo
    valorDb_ += alfa_ * (energiaDb - valorDb_);
  }

  bool  listo()   const { return listo_; }
  float valorDb() const { return valorDb_; }

private:
  uint32_t bloqueMs_;
  float    alfa_ = 0.0016f;
  bool     listo_ = false;
  uint32_t bloquesIniciales_ = 0;
  double   sumaInicial_ = 0;
  float    valorDb_ = 0;
};

// ============================================================================
//  ReglaChoque  (Regla 1, seccion 3.4)
//  ---------------------------------------------------------------------------
//  SI |a| >= choque_g  Y  sonido >= choque_db  Y  |t_a - t_s| <= 500 ms
//        -> CHOQUE                  (nivel ALERTA)
//  SI solo |a| >= choque_g (sin sonido en +-500 ms)
//        -> IMPACTO_SIN_CONFIRMAR   (nivel REGISTRO)
//  SI solo sonido fuerte
//        -> se ignora (solo cuenta para el log serie)
//
//  El sonido puede llegar ANTES o DESPUES del golpe: se guarda el ultimo
//  instante de sonido fuerte y, al detectar el pico de aceleracion, se mira
//  hacia atras; si todavia no hubo sonido se espera hasta que venza la ventana.
//  El pico |a| se sigue midiendo 100 ms despues de t_a para reportar a_pico_g.
//  Antirrebote: un evento de esta regla cada 10 s como maximo.
// ============================================================================
enum class DecisionChoque : uint8_t { NINGUNA = 0, CHOQUE, IMPACTO_SIN_CONFIRMAR };

struct EvidenciaChoque {
  float   aPicoG    = 0;
  float   splPicoDb = 0;
  int32_t deltaMs   = 0;      // t_s - t_a (negativo: el sonido llego antes)
  bool    hayDelta  = false;  // false -> delta_ms se reporta como null
  int64_t tsMs      = 0;      // instante del evento = t_a
};

class ReglaChoque {
public:
  static constexpr uint32_t VENTANA_PICO_MS = 100;    // se sigue midiendo el pico
  static constexpr uint32_t ANTIRREBOTE_MS  = 10000;  // 10 s

  explicit ReglaChoque(const UmbralesDeteccion& u = UmbralesDeteccion()) : u_(u) {}

  void setUmbrales(const UmbralesDeteccion& u) { u_ = u; }
  void reiniciar() {
    pendiente_ = false; sonidoConfirmado_ = false;
    tsSonidoFuerte_ = 0; haySonidoFuerte_ = false;
    ultimoEventoMs_ = 0; sonidosIgnorados_ = 0;
  }

  /// Llamar con cada muestra del acelerometro (500 Hz). |a| en g.
  void muestraAceleracion(float moduloG, int64_t tsMs) {
    if (pendiente_) {
      if (moduloG > aPico_) aPico_ = moduloG;
      return;
    }
    if (moduloG < u_.choqueG) return;
    if (ultimoEventoMs_ != 0 && tsMs - ultimoEventoMs_ < (int64_t)ANTIRREBOTE_MS) return;

    pendiente_        = true;
    tsImpacto_        = tsMs;
    aPico_            = moduloG;
    splPico_          = 0;
    sonidoConfirmado_ = false;
    // El sonido puede haber llegado ANTES del golpe: se mira hacia atras.
    if (haySonidoFuerte_ && tsMs - tsSonidoFuerte_ <= (int64_t)u_.choqueVentanaMs) {
      sonidoConfirmado_ = true;
      splPico_          = splSonidoFuerte_;
      tsSonido_         = tsSonidoFuerte_;
    }
  }

  /// Llamar con cada bloque de audio (16 ms). splDb = dB SPL del bloque.
  void bloqueAudio(float splDb, int64_t tsMs) {
    if (splDb < u_.choqueDb) return;

    haySonidoFuerte_  = true;
    tsSonidoFuerte_   = tsMs;
    splSonidoFuerte_  = splDb;

    if (!pendiente_) { sonidosIgnorados_++; return; }  // solo sonido -> se ignora
    if (sonidoConfirmado_) {
      if (splDb > splPico_) splPico_ = splDb;
      return;
    }
    if (tsMs - tsImpacto_ <= (int64_t)u_.choqueVentanaMs) {
      sonidoConfirmado_ = true;
      splPico_          = splDb;
      tsSonido_         = tsMs;
    }
  }

  /**
   * Llamar periodicamente (p. ej. en el loop, cada pocos ms). Devuelve la
   * decision cuando se cumple la condicion y ya cerro la ventana del pico, o
   * cuando vence la ventana de 500 ms sin sonido.
   */
  DecisionChoque evaluar(int64_t tsMs, EvidenciaChoque& evidencia) {
    if (!pendiente_) return DecisionChoque::NINGUNA;

    const int64_t transcurrido = tsMs - tsImpacto_;
    const int64_t esperaPico   = (int64_t)VENTANA_PICO_MS;
    const int64_t esperaVentana = (int64_t)u_.choqueVentanaMs;

    if (sonidoConfirmado_ && transcurrido >= esperaPico)
      return cerrar(DecisionChoque::CHOQUE, evidencia, true);

    if (!sonidoConfirmado_ && transcurrido >= (esperaVentana > esperaPico ? esperaVentana : esperaPico))
      return cerrar(DecisionChoque::IMPACTO_SIN_CONFIRMAR, evidencia, false);

    return DecisionChoque::NINGUNA;
  }

  bool     impactoPendiente()  const { return pendiente_; }
  uint32_t sonidosIgnorados()  const { return sonidosIgnorados_; }

private:
  DecisionChoque cerrar(DecisionChoque decision, EvidenciaChoque& evidencia, bool conSonido) {
    evidencia.aPicoG    = aPico_;
    evidencia.splPicoDb = conSonido ? splPico_ : 0.0f;
    evidencia.hayDelta  = conSonido;
    evidencia.deltaMs   = conSonido ? (int32_t)(tsSonido_ - tsImpacto_) : 0;
    evidencia.tsMs      = tsImpacto_;

    ultimoEventoMs_   = tsImpacto_;
    pendiente_        = false;
    sonidoConfirmado_ = false;
    haySonidoFuerte_  = false;   // el sonido de este golpe no sirve para el siguiente
    return decision;
  }

  UmbralesDeteccion u_;
  bool    pendiente_        = false;
  bool    sonidoConfirmado_ = false;
  bool    haySonidoFuerte_  = false;
  int64_t tsImpacto_        = 0;
  int64_t tsSonido_         = 0;
  int64_t tsSonidoFuerte_   = 0;
  int64_t ultimoEventoMs_   = 0;
  float   aPico_            = 0;
  float   splPico_          = 0;
  float   splSonidoFuerte_  = 0;
  uint32_t sonidosIgnorados_ = 0;
};

// ============================================================================
//  ReglaRotura  (Regla 2, seccion 3.4) — maquina de estados
//  ---------------------------------------------------------------------------
//  1. GOLPE      energia 150-400 Hz > piso_baja + 15 dB  (o vibracion > 0.15 g)
//  2. ESTALLIDO  en <= 300 ms:  energia 3-7 kHz > piso_alta + 12 dB
//                               Y planitud >= 0.3
//  3. DURACION   estallido sostenido >= 150 ms (tolera 1 bloque aislado malo)
//  4. FUSION     vibracion > 0.15 g dentro de +-500 ms de t_golpe
//                   -> ROTURA_VENTANA        (ALERTA)
//                sin vibracion
//                   -> VIDRIO_ROTO_EXTERNO   (REGISTRO)
//                vibracion > 0.15 g sin estallido en 1 s
//                   -> GOLPE_MANIPULACION    (ALERTA_LEVE)
//
//  Un golpe SOLO acustico sin estallido en 300 ms se descarta sin evento.
//  Antirrebote: un evento de esta regla cada 15 s como maximo.
//
//  La clase es dueña de los dos EstimadorPiso (uno por banda): el .ino llama a
//  bloque() SIEMPRE (tambien desarmado o en marcha) para que el piso siga
//  aprendiendo, y pasa habilitada=false cuando la regla no debe decidir.
// ============================================================================
enum class DecisionRotura : uint8_t {
  NINGUNA = 0, ROTURA_VENTANA, VIDRIO_ROTO_EXTERNO, GOLPE_MANIPULACION
};

struct EvidenciaRotura {
  float    eBajaDb     = 0;
  float    pisoBajaDb  = 0;
  float    eAltaDb     = 0;
  float    pisoAltaDb  = 0;
  float    planitud    = 0;
  uint32_t duracionMs  = 0;
  float    vibG        = 0;
  int32_t  deltaVibMs  = 0;      // t_vibracion - t_golpe
  bool     hayVib      = false;
  int64_t  tsMs        = 0;      // instante del evento = t_golpe
};

class ReglaRotura {
public:
  static constexpr uint32_t ANTIRREBOTE_MS   = 15000;  // 15 s
  static constexpr size_t   VIBRACIONES_MAX  = 16;     // buffer circular

  explicit ReglaRotura(const UmbralesDeteccion& u = UmbralesDeteccion(),
                       uint32_t bloqueMs = audio_cfg::BLOQUE_MS)
    : u_(u), bloqueMs_(bloqueMs ? bloqueMs : 1), pisoBaja_(bloqueMs), pisoAlta_(bloqueMs) {}

  void setUmbrales(const UmbralesDeteccion& u) { u_ = u; }
  void recalibrarPiso() { pisoBaja_.recalibrar(); pisoAlta_.recalibrar(); }

  const EstimadorPiso& pisoBaja() const { return pisoBaja_; }
  const EstimadorPiso& pisoAlta() const { return pisoAlta_; }

  /// Vibracion > umbral detectada por la tarea del IMU (|a| menos la media movil).
  void registrarVibracion(float vibG, int64_t tsMs) {
    if (vibG < u_.vibracionG) return;
    vibraciones_[indiceVib_] = Vibracion{tsMs, vibG};
    indiceVib_ = (indiceVib_ + 1) % VIBRACIONES_MAX;
    if (vibracionesGuardadas_ < VIBRACIONES_MAX) vibracionesGuardadas_++;

    // Una vibracion tambien cuenta como GOLPE (paso 1) y arma el temporizador
    // de GOLPE_MANIPULACION por si no llega ningun estallido.
    if (estado_ == Estado::INACTIVO) {
      estado_             = Estado::ESPERANDO_ESTALLIDO;
      tsGolpe_            = tsMs;
      golpeConVib_        = true;
      acusticoConfirmado_ = false;
      eBajaGolpe_         = pisoBaja_.valorDb();
    } else if (!golpeConVib_ && tsMs - tsGolpe_ <= (int64_t)u_.fusionMs) {
      golpeConVib_ = true;  // el golpe acustico se confirma con vibracion
    }
  }

  /**
   * Un bloque de audio ya analizado (16 ms).
   * @param habilitada  true solo en ESTACIONADO y armado; si es false el piso
   *                    sigue aprendiendo pero la regla no decide nada.
   */
  DecisionRotura bloque(const ResultadoBloque& b, bool habilitada, EvidenciaRotura& evidencia) {
    if (!b.valido) return DecisionRotura::NINGUNA;

    const bool deteccionEnCurso = (estado_ != Estado::INACTIVO);
    pisoBaja_.actualizar(b.eBajaDb, deteccionEnCurso);
    pisoAlta_.actualizar(b.eAltaDb, deteccionEnCurso);

    if (!habilitada) { estado_ = Estado::INACTIVO; return DecisionRotura::NINGUNA; }
    if (!pisoBaja_.listo() || !pisoAlta_.listo()) return DecisionRotura::NINGUNA;

    const bool enAntirrebote =
      ultimoEventoMs_ != 0 && b.tsMs - ultimoEventoMs_ < (int64_t)ANTIRREBOTE_MS;

    const bool hayEstallido =
      b.eAltaDb > pisoAlta_.valorDb() + u_.estallidoAltaDb && b.planitud >= u_.planitudMin;

    switch (estado_) {
      case Estado::INACTIVO: {
        if (enAntirrebote) break;
        // Paso 1: golpe acustico en la banda baja.
        if (b.eBajaDb > pisoBaja_.valorDb() + u_.golpeBajaDb) {
          estado_             = Estado::ESPERANDO_ESTALLIDO;
          tsGolpe_            = b.tsMs;
          eBajaGolpe_         = b.eBajaDb;
          acusticoConfirmado_ = true;
          golpeConVib_        = hayVibracionCerca(b.tsMs);
        }
        break;
      }

      case Estado::ESPERANDO_ESTALLIDO: {
        // Si el golpe lo inicio la vibracion del MPU y justo despues llega el
        // golpe acustico de la banda baja, se toma el instante ACUSTICO como
        // t_golpe (mejor resolucion temporal): la vibracion queda como
        // evidencia previa y delta_vib_ms sale negativo.
        if (golpeConVib_ && !acusticoConfirmado_ &&
            b.eBajaDb > pisoBaja_.valorDb() + u_.golpeBajaDb &&
            b.tsMs - tsGolpe_ <= (int64_t)u_.fusionMs) {
          tsGolpe_            = b.tsMs;
          eBajaGolpe_         = b.eBajaDb;
          acusticoConfirmado_ = true;
        }

        // Paso 2: el estallido debe aparecer dentro de la ventana.
        if (hayEstallido && b.tsMs - tsGolpe_ <= (int64_t)u_.estallidoVentanaMs) {
          estado_        = Estado::EN_ESTALLIDO;
          tsEstallidoIni_ = b.tsMs;
          tsEstallidoFin_ = b.tsMs;
          eAltaMax_       = b.eAltaDb;
          planitudMax_    = b.planitud;
          fallosTolerados_ = 0;
          break;
        }
        // Venció la ventana del estallido.
        if (b.tsMs - tsGolpe_ > (int64_t)u_.estallidoVentanaMs) {
          // Vibracion sola: se espera golpe_sin_estallido_ms antes de decidir.
          if (golpeConVib_ && b.tsMs - tsGolpe_ >= (int64_t)u_.golpeSinEstallidoMs)
            return cerrarManipulacion(evidencia, b);
          if (!golpeConVib_) estado_ = Estado::INACTIVO;  // golpe solo acustico: se descarta
        }
        break;
      }

      case Estado::EN_ESTALLIDO: {
        if (hayEstallido) {
          tsEstallidoFin_ = b.tsMs;
          if (b.eAltaDb > eAltaMax_)    eAltaMax_ = b.eAltaDb;
          if (b.planitud > planitudMax_) planitudMax_ = b.planitud;
        } else if (++fallosTolerados_ > 1) {
          // Dos bloques seguidos sin cumplir: termino el estallido.
          return cerrarEstallido(evidencia, b);
        }
        // Paso 3: ya alcanzo la duracion minima -> se resuelve de inmediato
        // (no hay motivo para hacer esperar la alerta).
        if (duracionEstallidoMs() >= u_.estallidoMinMs)
          return cerrarEstallido(evidencia, b);
        break;
      }
    }
    return DecisionRotura::NINGUNA;
  }

  /**
   * Temporizadores que no dependen de que llegue un bloque de audio: cierra
   * GOLPE_MANIPULACION cuando pasa golpe_sin_estallido_ms desde una vibracion
   * sin estallido. Llamar desde el loop.
   */
  DecisionRotura evaluar(int64_t tsMs, EvidenciaRotura& evidencia) {
    if (estado_ != Estado::ESPERANDO_ESTALLIDO) return DecisionRotura::NINGUNA;
    if (!golpeConVib_) return DecisionRotura::NINGUNA;
    if (tsMs - tsGolpe_ < (int64_t)u_.golpeSinEstallidoMs) return DecisionRotura::NINGUNA;

    ResultadoBloque vacio;
    vacio.tsMs    = tsMs;
    vacio.eBajaDb = eBajaGolpe_;
    vacio.eAltaDb = pisoAlta_.valorDb();
    return cerrarManipulacion(evidencia, vacio);
  }

  bool deteccionEnCurso() const { return estado_ != Estado::INACTIVO; }

private:
  enum class Estado : uint8_t { INACTIVO = 0, ESPERANDO_ESTALLIDO, EN_ESTALLIDO };
  struct Vibracion { int64_t tsMs; float g; };

  uint32_t duracionEstallidoMs() const {
    return (uint32_t)(tsEstallidoFin_ - tsEstallidoIni_) + bloqueMs_;
  }

  /// Busca la vibracion mas cercana a tsGolpe dentro de +-fusion_ms.
  bool vibracionCercana(int64_t tsGolpe, float& vibG, int32_t& deltaMs) const {
    bool encontrada = false;
    int64_t mejorDelta = 0;
    float mejorG = 0;
    for (size_t i = 0; i < vibracionesGuardadas_; i++) {
      const int64_t delta = vibraciones_[i].tsMs - tsGolpe;
      const int64_t abs_ = delta < 0 ? -delta : delta;
      if (abs_ > (int64_t)u_.fusionMs) continue;
      if (!encontrada || abs_ < (mejorDelta < 0 ? -mejorDelta : mejorDelta)) {
        encontrada = true;
        mejorDelta = delta;
        mejorG     = vibraciones_[i].g;
      }
    }
    if (encontrada) { vibG = mejorG; deltaMs = (int32_t)mejorDelta; }
    return encontrada;
  }

  bool hayVibracionCerca(int64_t tsGolpe) const {
    float g; int32_t d;
    return vibracionCercana(tsGolpe, g, d);
  }

  DecisionRotura cerrarEstallido(EvidenciaRotura& evidencia, const ResultadoBloque& b) {
    evidencia.eBajaDb    = eBajaGolpe_;
    evidencia.pisoBajaDb = pisoBaja_.valorDb();
    evidencia.eAltaDb    = eAltaMax_;
    evidencia.pisoAltaDb = pisoAlta_.valorDb();
    evidencia.planitud   = planitudMax_;
    evidencia.duracionMs = duracionEstallidoMs();
    evidencia.tsMs       = tsGolpe_;
    evidencia.hayVib     = vibracionCercana(tsGolpe_, evidencia.vibG, evidencia.deltaVibMs);

    const bool duracionSuficiente = evidencia.duracionMs >= u_.estallidoMinMs;
    estado_ = Estado::INACTIVO;
    if (!duracionSuficiente) return DecisionRotura::NINGUNA;  // estallido demasiado corto

    ultimoEventoMs_ = b.tsMs;
    return evidencia.hayVib ? DecisionRotura::ROTURA_VENTANA
                            : DecisionRotura::VIDRIO_ROTO_EXTERNO;
  }

  DecisionRotura cerrarManipulacion(EvidenciaRotura& evidencia, const ResultadoBloque& b) {
    evidencia.eBajaDb    = eBajaGolpe_;
    evidencia.pisoBajaDb = pisoBaja_.valorDb();
    evidencia.eAltaDb    = b.eAltaDb;
    evidencia.pisoAltaDb = pisoAlta_.valorDb();
    evidencia.planitud   = 0;
    evidencia.duracionMs = 0;
    evidencia.tsMs       = tsGolpe_;
    evidencia.hayVib     = vibracionCercana(tsGolpe_, evidencia.vibG, evidencia.deltaVibMs);

    estado_         = Estado::INACTIVO;
    ultimoEventoMs_ = b.tsMs;
    return DecisionRotura::GOLPE_MANIPULACION;
  }

  UmbralesDeteccion u_;
  uint32_t      bloqueMs_;
  EstimadorPiso pisoBaja_;
  EstimadorPiso pisoAlta_;

  Estado   estado_          = Estado::INACTIVO;
  int64_t  tsGolpe_         = 0;
  int64_t  tsEstallidoIni_  = 0;
  int64_t  tsEstallidoFin_  = 0;
  int64_t  ultimoEventoMs_  = 0;
  float    eBajaGolpe_      = 0;
  float    eAltaMax_        = 0;
  float    planitudMax_     = 0;
  uint8_t  fallosTolerados_ = 0;
  bool     golpeConVib_        = false;
  bool     acusticoConfirmado_ = false;

  Vibracion vibraciones_[VIBRACIONES_MAX] = {};
  size_t    indiceVib_ = 0;
  size_t    vibracionesGuardadas_ = 0;
};

// ============================================================================
//  MaquinaModo  (seccion 3.3)
//  ---------------------------------------------------------------------------
//  Arranca en EN_MARCHA.
//
//  CON senal de contacto (cfg::USAR_CONTACTO = true, prioridad sobre lo
//  inercial, fuente "CONTACTO"): contacto encendido -> EN_MARCHA, apagado ->
//  ESTACIONADO, con antirrebote de 2 s.
//
//  SIN senal de contacto (fuente "INERCIAL"), por ventanas de 1 s del MPU:
//      variacion = max(|a|) - min(|a|) de la ventana
//      variacion <  quieto_g durante quieto_s seguidos  -> ESTACIONADO
//      variacion >= quieto_g durante marcha_s seguidos  -> EN_MARCHA
// ============================================================================
class MaquinaModo {
public:
  static constexpr uint32_t ANTIRREBOTE_CONTACTO_MS = 2000;  // 2 s

  explicit MaquinaModo(const UmbralesDeteccion& u = UmbralesDeteccion(), bool usarContacto = false)
    : u_(u), usarContacto_(usarContacto) {}

  void setUmbrales(const UmbralesDeteccion& u) { u_ = u; }
  void setUsarContacto(bool usar) { usarContacto_ = usar; }

  Modo       modo()   const { return modo_; }
  FuenteModo fuente() const { return fuente_; }
  bool       usaContacto() const { return usarContacto_; }
  uint32_t   segundosQuieto() const { return segQuieto_; }
  uint32_t   segundosMovimiento() const { return segMovimiento_; }

  /// Comando MQTT {"accion":"forzar_modo","modo":"EN_MARCHA"} (pruebas).
  bool forzarModo(Modo nuevo) {
    segQuieto_ = segMovimiento_ = 0;
    fuente_ = FuenteModo::FORZADO;
    if (modo_ == nuevo) return false;
    modo_ = nuevo;
    return true;
  }

  /**
   * Senal de contacto (ACC/IGN) leida por cfg::PIN_CONTACTO. Tiene PRIORIDAD
   * sobre la regla inercial. Devuelve true si el modo cambio.
   */
  bool senalContacto(bool encendido, int64_t tsMs) {
    if (!usarContacto_) return false;

    if (encendido != lecturaContacto_) {   // la senal cambio: arranca el antirrebote
      lecturaContacto_ = encendido;
      tsCambioContacto_ = tsMs;
      contactoEstable_ = false;
      return false;
    }
    if (contactoEstable_) return false;
    if (tsMs - tsCambioContacto_ < (int64_t)ANTIRREBOTE_CONTACTO_MS) return false;

    contactoEstable_ = true;
    const Modo nuevo = encendido ? Modo::EN_MARCHA : Modo::ESTACIONADO;
    fuente_ = FuenteModo::CONTACTO;
    segQuieto_ = segMovimiento_ = 0;
    if (modo_ == nuevo) return false;
    modo_ = nuevo;
    return true;
  }

  /**
   * Una ventana de 1 s del acelerometro. Devuelve true si el modo cambio.
   * @param variacionG  max(|a|) - min(|a|) de la ventana, en g
   */
  bool ventana(float variacionG, int64_t /*tsMs*/ = 0) {
    // Con contacto habilitado y estable, la regla inercial no manda.
    if (usarContacto_ && contactoEstable_) return false;

    if (variacionG < u_.quietoG) {
      segMovimiento_ = 0;
      if (++segQuieto_ >= u_.quietoS && modo_ != Modo::ESTACIONADO) {
        modo_ = Modo::ESTACIONADO;
        fuente_ = FuenteModo::INERCIAL;
        segQuieto_ = 0;
        return true;
      }
    } else {
      segQuieto_ = 0;
      if (++segMovimiento_ >= u_.marchaS && modo_ != Modo::EN_MARCHA) {
        modo_ = Modo::EN_MARCHA;
        fuente_ = FuenteModo::INERCIAL;
        segMovimiento_ = 0;
        return true;
      }
    }
    return false;
  }

private:
  UmbralesDeteccion u_;
  bool       usarContacto_     = false;
  Modo       modo_             = Modo::EN_MARCHA;
  FuenteModo fuente_           = FuenteModo::INERCIAL;
  uint32_t   segQuieto_        = 0;
  uint32_t   segMovimiento_    = 0;
  bool       lecturaContacto_  = true;
  bool       contactoEstable_  = false;
  int64_t    tsCambioContacto_ = 0;
};

#endif  // REGLAS_DETECCION_H
