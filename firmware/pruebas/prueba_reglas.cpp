/*
 * ============================================================================
 *  PRUEBAS DE HOST DE LAS REGLAS DE DETECCION (sin hardware)         v3.0.0
 *  Tesis: Sistema inteligente IoT con YOLO para monitoreo vehicular (UCSM)
 * ============================================================================
 *  Compila y ejecuta las clases puras de
 *      firmware\nodo_iot_esp32s3\reglas_deteccion.h
 *  contra senales SINTETICAS a 16 kHz, para validar las reglas definitivas de
 *  la seccion 3.4 antes de tocar el ESP32-S3.
 *
 *  Compilar y ejecutar:
 *      firmware\pruebas\compilar_pruebas.bat
 *  o a mano:
 *      g++ -std=c++17 -O2 -I..\nodo_iot_esp32s3 prueba_reglas.cpp -o prueba_reglas.exe
 *
 *  Imprime PASA/FALLA por caso y termina con codigo != 0 si algo falla.
 * ============================================================================
 */
#include "reglas_deteccion.h"

#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <vector>

// ============================================================================
//  Marco de pruebas minimo
// ============================================================================
class Reporte {
public:
  void verificar(const char* grupo, const char* caso, bool condicion, const char* detalle = "") {
    total_++;
    if (condicion) {
      printf("  [PASA ] %-10s %-52s %s\n", grupo, caso, detalle);
    } else {
      fallos_++;
      printf("  [FALLA] %-10s %-52s %s\n", grupo, caso, detalle);
    }
  }

  void titulo(const char* texto) const { printf("\n== %s ==\n", texto); }

  int resumen() const {
    printf("\n------------------------------------------------------------------\n");
    printf(" %d casos, %d fallos\n", total_, fallos_);
    printf("------------------------------------------------------------------\n");
    if (fallos_ == 0) printf(" RESULTADO: TODAS LAS PRUEBAS PASAN\n");
    else              printf(" RESULTADO: HAY %d FALLO(S)\n", fallos_);
    return fallos_ == 0 ? 0 : 1;
  }

private:
  int total_ = 0;
  int fallos_ = 0;
};

// ============================================================================
//  GeneradorSenal  (senales sinteticas en cuentas de 24 bits, 16 kHz)
//  ---------------------------------------------------------------------------
//  Todas las amplitudes se piden en dB respecto al fondo de escala (dBFS), de
//  modo que "dB SPL = 120 + dBFS" sale directo. Un generador congruencial
//  propio mantiene las pruebas 100 % reproducibles.
// ============================================================================
class GeneradorSenal {
public:
  explicit GeneradorSenal(uint32_t semilla = 20260101u) : estado_(semilla) {}

  static float amplitudDesdeDbfs(float dbfs) {
    return audio_cfg::FONDO_ESCALA * powf(10.0f, dbfs / 20.0f);
  }

  /// Silencio con un piso de ruido muy bajo (el I2S nunca entrega ceros).
  void silencio(float* bloque, size_t n, float dbfs = -95.0f) {
    const float a = amplitudDesdeDbfs(dbfs);
    for (size_t i = 0; i < n; i++) bloque[i] = a * aleatorio();
  }

  /// Ruido blanco de banda completa (ruido ambiente del estacionamiento).
  void ruidoBlanco(float* bloque, size_t n, float dbfs) {
    const float a = amplitudDesdeDbfs(dbfs);
    for (size_t i = 0; i < n; i++) bloque[i] = a * aleatorio();
  }

  /// Tono puro: para el golpe grave (200 Hz) y el caso de planitud baja (5 kHz).
  void tono(float* bloque, size_t n, float frecuenciaHz, float dbfs, float dbfsFondo = -95.0f) {
    const float a = amplitudDesdeDbfs(dbfs);
    const float fondo = amplitudDesdeDbfs(dbfsFondo);
    for (size_t i = 0; i < n; i++) {
      const float t = (float)(muestrasEmitidas_ + i) / (float)audio_cfg::MUESTREO_HZ;
      bloque[i] = a * sinf(2.0f * PI_REGLAS * frecuenciaHz * t) + fondo * aleatorio();
    }
    muestrasEmitidas_ += n;
  }

  /**
   * Ruido de banda limitada: suma de senoidales en los bins de [fIni, fFin]
   * con fases fijas pseudoaleatorias. Da planitud espectral alta (~1) dentro
   * de la banda, que es lo que caracteriza el estallido del vidrio templado.
   */
  void ruidoBanda(float* bloque, size_t n, float fIniHz, float fFinHz, float dbfs,
                  float dbfsFondo = -95.0f) {
    const size_t binIni = (size_t)(fIniHz / audio_cfg::RESOLUCION_HZ);
    const size_t binFin = (size_t)(fFinHz / audio_cfg::RESOLUCION_HZ);
    const size_t comp = binFin >= binIni ? binFin - binIni + 1 : 1;
    const float a = amplitudDesdeDbfs(dbfs) / sqrtf((float)comp);
    const float fondo = amplitudDesdeDbfs(dbfsFondo);

    for (size_t i = 0; i < n; i++) {
      const float t = (float)(muestrasEmitidas_ + i) / (float)audio_cfg::MUESTREO_HZ;
      float suma = 0;
      for (size_t k = binIni; k <= binFin; k++) {
        const float f = (float)k * audio_cfg::RESOLUCION_HZ;
        suma += sinf(2.0f * PI_REGLAS * f * t + fase(k));
      }
      bloque[i] = a * suma + fondo * aleatorio();
    }
    muestrasEmitidas_ += n;
  }

  /// Mezcla dos bloques ya generados (golpe grave + estallido simultaneos).
  static void mezclar(float* destino, const float* otro, size_t n) {
    for (size_t i = 0; i < n; i++) destino[i] += otro[i];
  }

  void reiniciarTiempo() { muestrasEmitidas_ = 0; }

private:
  /// Aleatorio uniforme en [-1, 1) con un LCG determinista.
  float aleatorio() {
    estado_ = estado_ * 1664525u + 1013904223u;
    return ((float)(estado_ >> 8) / 8388608.0f) - 1.0f;
  }

  /// Fase fija por bin: el mismo bin siempre tiene la misma fase.
  static float fase(size_t k) {
    return fmodf((float)k * 2.39996323f, 2.0f * PI_REGLAS);
  }

  uint32_t estado_;
  uint64_t muestrasEmitidas_ = 0;
};

// ============================================================================
//  GRUPO 1 — Regla 1: CHOQUE
//  ---------------------------------------------------------------------------
//  Se simula una linea de tiempo milisegundo a milisegundo: el acelerometro
//  entrega una muestra cada 2 ms (500 Hz) y el audio un bloque cada 16 ms.
// ============================================================================
//  NOTA: tsSonidoMs debe ser multiplo de 16 ms (la rejilla real de bloques de
//  audio); el arnes alinea el valor y avisa si no lo era.
struct GuionChoque {
  int64_t tsImpactoMs = -1;   // instante del pico de aceleracion (-1 = sin impacto)
  float   aPicoG      = 0;
  int64_t tsSonidoMs  = -1;   // instante del bloque de audio fuerte
  float   splDb       = 0;
  int64_t duracionMs  = 3000;
};

struct ResultadoChoque {
  DecisionChoque  decision  = DecisionChoque::NINGUNA;
  EvidenciaChoque evidencia;
  uint32_t        ignorados = 0;
  int             decisiones = 0;
};

static ResultadoChoque simularChoque(const GuionChoque& g, const UmbralesDeteccion& u) {
  ReglaChoque regla(u);
  ResultadoChoque salida;

  for (int64_t ms = 0; ms <= g.duracionMs; ms++) {
    // --- Acelerometro a 500 Hz (una muestra cada 2 ms) ---
    if (ms % 2 == 0) {
      float moduloG = 1.0f;  // en reposo el MPU mide 1 g (la gravedad)
      if (g.tsImpactoMs >= 0) {
        // Pulso de impacto: sube al pico y decae en ~40 ms.
        const int64_t d = ms - g.tsImpactoMs;
        if (d >= 0 && d < 40) moduloG = g.aPicoG * expf(-(float)d / 12.0f);
        if (moduloG < 1.0f) moduloG = 1.0f;
      }
      regla.muestraAceleracion(moduloG, ms);
    }

    // --- Audio: un bloque cada 16 ms ---
    if (ms % (int64_t)audio_cfg::BLOQUE_MS == 0) {
      float spl = 45.0f;  // estacionamiento tranquilo
      if (g.tsSonidoMs >= 0 && ms == g.tsSonidoMs) spl = g.splDb;
      regla.bloqueAudio(spl, ms);
    }

    // --- Orquestacion (en el firmware esto vive en loop()) ---
    EvidenciaChoque evidencia;
    const DecisionChoque d = regla.evaluar(ms, evidencia);
    if (d != DecisionChoque::NINGUNA) {
      salida.decisiones++;
      if (salida.decision == DecisionChoque::NINGUNA) {
        salida.decision = d;
        salida.evidencia = evidencia;
      }
    }
  }
  salida.ignorados = regla.sonidosIgnorados();
  return salida;
}

static void grupo1Choque(Reporte& r) {
  r.titulo("GRUPO 1 - Regla 1: CHOQUE / IMPACTO_SIN_CONFIRMAR");
  const UmbralesDeteccion u;
  char detalle[160];

  // --- Caso 1.1: pico de 6 g + 115 dB SPL a 40 ms -> CHOQUE ---
  {
    GuionChoque g;
    g.tsImpactoMs = 1000; g.aPicoG = 6.0f;
    g.tsSonidoMs  = 1040; g.splDb  = 115.0f;
    const ResultadoChoque s = simularChoque(g, u);

    snprintf(detalle, sizeof(detalle), "a_pico=%.2f g  spl=%.1f dB  delta=%d ms",
             s.evidencia.aPicoG, s.evidencia.splPicoDb, s.evidencia.deltaMs);
    r.verificar("1.1", "pico 6 g + 115 dB a 40 ms -> CHOQUE",
                s.decision == DecisionChoque::CHOQUE, detalle);
    r.verificar("1.1", "evidencia: a_pico_g cercano a 6 g",
                s.evidencia.aPicoG >= 5.5f && s.evidencia.aPicoG <= 6.5f, "");
    r.verificar("1.1", "evidencia: delta_ms = 40 (el sonido llego despues)",
                s.evidencia.hayDelta && s.evidencia.deltaMs == 40, "");
    r.verificar("1.1", "un solo evento (antirrebote de 10 s)", s.decisiones == 1, "");
  }

  // --- Caso 1.2: el sonido llega 40 ms ANTES del golpe -> CHOQUE ---
  {
    GuionChoque g;
    g.tsImpactoMs = 1048; g.aPicoG = 6.0f;
    g.tsSonidoMs  = 1008; g.splDb  = 115.0f;   // 1008 = 63 bloques de 16 ms
    const ResultadoChoque s = simularChoque(g, u);

    snprintf(detalle, sizeof(detalle), "delta=%d ms", s.evidencia.deltaMs);
    r.verificar("1.2", "sonido 40 ms ANTES del golpe -> CHOQUE",
                s.decision == DecisionChoque::CHOQUE, detalle);
    r.verificar("1.2", "evidencia: delta_ms negativo",
                s.evidencia.hayDelta && s.evidencia.deltaMs == -40, "");
  }

  // --- Caso 1.3: sonido a 800 ms (fuera de la ventana) -> IMPACTO_SIN_CONFIRMAR ---
  {
    GuionChoque g;
    g.tsImpactoMs = 1000; g.aPicoG = 6.0f;
    g.tsSonidoMs  = 1808; g.splDb  = 115.0f;   // 808 ms despues del golpe
    const ResultadoChoque s = simularChoque(g, u);

    snprintf(detalle, sizeof(detalle), "a_pico=%.2f g  hay_delta=%s",
             s.evidencia.aPicoG, s.evidencia.hayDelta ? "si" : "no");
    r.verificar("1.3", "pico 6 g + sonido a 800 ms -> IMPACTO_SIN_CONFIRMAR",
                s.decision == DecisionChoque::IMPACTO_SIN_CONFIRMAR, detalle);
    r.verificar("1.3", "evidencia: delta_ms nulo (no hubo sonido en la ventana)",
                !s.evidencia.hayDelta, "");
  }

  // --- Caso 1.4: solo sonido fuerte, sin impacto -> nada ---
  {
    GuionChoque g;
    g.tsImpactoMs = -1;
    g.tsSonidoMs  = 1008; g.splDb = 118.0f;
    const ResultadoChoque s = simularChoque(g, u);

    snprintf(detalle, sizeof(detalle), "ignorados=%u", s.ignorados);
    r.verificar("1.4", "solo sonido fuerte (118 dB) -> ningun evento",
                s.decision == DecisionChoque::NINGUNA, detalle);
    r.verificar("1.4", "el sonido queda contado como ignorado (log serie)",
                s.ignorados >= 1, "");
  }

  // --- Caso 1.5: golpe por debajo del umbral (3 g) -> nada ---
  {
    GuionChoque g;
    g.tsImpactoMs = 1000; g.aPicoG = 3.0f;
    g.tsSonidoMs  = 1040; g.splDb  = 115.0f;
    const ResultadoChoque s = simularChoque(g, u);
    r.verificar("1.5", "pico de 3 g (< 4 g) -> ningun evento",
                s.decision == DecisionChoque::NINGUNA, "");
  }

  // --- Caso 1.6: sonido de 100 dB (< 112 dB) -> IMPACTO_SIN_CONFIRMAR ---
  {
    GuionChoque g;
    g.tsImpactoMs = 1000; g.aPicoG = 6.8f;
    g.tsSonidoMs  = 1040; g.splDb  = 100.0f;
    const ResultadoChoque s = simularChoque(g, u);
    r.verificar("1.6", "pico 6.8 g + solo 100 dB -> IMPACTO_SIN_CONFIRMAR",
                s.decision == DecisionChoque::IMPACTO_SIN_CONFIRMAR, "");
  }
}

// ============================================================================
//  GRUPO 2 — Regla 2: ROTURA DE VENTANA
//  ---------------------------------------------------------------------------
//  Escenario comun: 3 s de ruido de fondo para que el EstimadorPiso aprenda el
//  piso de ambas bandas, y despues el guion de cada caso.
// ============================================================================
constexpr float DBFS_FONDO       = -62.0f;   // ruido ambiente del estacionamiento
constexpr float DBFS_GOLPE_BAJO  = -22.0f;   // golpe grave de 200 Hz
constexpr float DBFS_ESTALLIDO   = -26.0f;   // estallido de banda ancha 3-7 kHz
constexpr size_t BLOQUES_APRENDIZAJE = 220;  // 3.5 s (el piso necesita 2 s)

//  Los instantes del guion son RELATIVOS al final del aprendizaje del piso
//  (t = 0 es el primer bloque "util"), y deben ser multiplos de 16 ms.
struct GuionRotura {
  int64_t tsGolpeMs      = 2000;   // golpe grave (-1 = sin golpe acustico)
  int64_t duracionEstallidoMs = 0; // 0 = sin estallido
  bool    estallidoTono  = false;  // true = tono puro de 5 kHz (planitud baja)
  int64_t tsVibracionMs  = -1;     // vibracion del MPU (-1 = ninguna)
  float   vibracionG     = 0;
  int64_t duracionMs     = 5000;
};

struct ResultadoRotura {
  DecisionRotura  decision = DecisionRotura::NINGUNA;
  EvidenciaRotura evidencia;
  int             decisiones = 0;
  int64_t         tsEventoRelMs = -1;   // evidencia.tsMs en tiempo del guion
  float           pisoBajaDb = 0;
  float           pisoAltaDb = 0;
};

static ResultadoRotura simularRotura(const GuionRotura& g, const UmbralesDeteccion& u) {
  GeneradorSenal gen;
  AnalizadorEspectral analizador;
  ReglaRotura regla(u);
  ResultadoRotura salida;

  float bloque[audio_cfg::FFT_PUNTOS];
  float extra[audio_cfg::FFT_PUNTOS];
  const size_t n = audio_cfg::FFT_PUNTOS;

  // --- 1) Aprendizaje del piso de ruido (el EstimadorPiso necesita 2 s) ---
  //  Se arranca en un instante grande para que el antirrebote por "ultimo
  //  evento = 0" no interfiera, igual que en el nodo real.
  const int64_t TS_INICIO = 100000;
  int64_t ts = TS_INICIO;
  for (size_t i = 0; i < BLOQUES_APRENDIZAJE; i++, ts += audio_cfg::BLOQUE_MS) {
    gen.ruidoBlanco(bloque, n, DBFS_FONDO);
    EvidenciaRotura ev;
    regla.bloque(analizador.analizar(bloque, n, ts), true, ev);
  }

  // --- 2) Guion del caso (tsBase = el "t = 0" del guion) ---
  const int64_t tsBase = ts;
  for (int64_t rel = 0; rel <= g.duracionMs; rel += audio_cfg::BLOQUE_MS) {
    ts = tsBase + rel;
    gen.ruidoBlanco(bloque, n, DBFS_FONDO);

    const bool enGolpe = g.tsGolpeMs >= 0 && rel >= g.tsGolpeMs &&
                         rel < g.tsGolpeMs + 48;  // el golpe grave dura ~3 bloques
    if (enGolpe) {
      gen.tono(extra, n, 200.0f, DBFS_GOLPE_BAJO);
      GeneradorSenal::mezclar(bloque, extra, n);
    }

    // El estallido arranca 32 ms despues del golpe (2 bloques).
    const int64_t tsEstallido = (g.tsGolpeMs >= 0 ? g.tsGolpeMs : g.tsVibracionMs) + 32;
    const bool enEstallido = g.duracionEstallidoMs > 0 && rel >= tsEstallido &&
                             rel < tsEstallido + g.duracionEstallidoMs;
    if (enEstallido) {
      if (g.estallidoTono) gen.tono(extra, n, 5000.0f, DBFS_ESTALLIDO);
      else                 gen.ruidoBanda(extra, n, 3000.0f, 7000.0f, DBFS_ESTALLIDO);
      GeneradorSenal::mezclar(bloque, extra, n);
    }

    // Vibracion del MPU (la tarea IMU la publica en su buffer circular): se
    // registra una sola vez, en el bloque que cubre su instante.
    if (g.tsVibracionMs >= 0 && rel >= g.tsVibracionMs &&
        rel - (int64_t)audio_cfg::BLOQUE_MS < g.tsVibracionMs)
      regla.registrarVibracion(g.vibracionG, tsBase + g.tsVibracionMs);

    EvidenciaRotura ev;
    DecisionRotura d = regla.bloque(analizador.analizar(bloque, n, ts), true, ev);
    if (d == DecisionRotura::NINGUNA) d = regla.evaluar(ts, ev);
    if (d != DecisionRotura::NINGUNA) {
      salida.decisiones++;
      if (salida.decision == DecisionRotura::NINGUNA) {
        salida.decision = d;
        salida.evidencia = ev;
        salida.tsEventoRelMs = ev.tsMs - tsBase;
      }
    }
  }

  salida.pisoBajaDb = regla.pisoBaja().valorDb();
  salida.pisoAltaDb = regla.pisoAlta().valorDb();
  return salida;
}

static void grupo2Rotura(Reporte& r) {
  r.titulo("GRUPO 2 - Regla 2: ROTURA_VENTANA / VIDRIO_ROTO_EXTERNO / GOLPE_MANIPULACION");
  const UmbralesDeteccion u;
  char detalle[200];

  // --- Caso 2.1: golpe grave + estallido de 250 ms CON vibracion -> ROTURA_VENTANA ---
  {
    GuionRotura g;
    g.tsGolpeMs = 2000; g.duracionEstallidoMs = 250;
    g.tsVibracionMs = 1968; g.vibracionG = 0.31f;   // 32 ms antes del golpe
    const ResultadoRotura s = simularRotura(g, u);

    snprintf(detalle, sizeof(detalle),
             "e_baja=%.1f piso=%.1f e_alta=%.1f piso=%.1f plan=%.2f dur=%u ms vib=%.2f g d=%d ms",
             s.evidencia.eBajaDb, s.evidencia.pisoBajaDb, s.evidencia.eAltaDb,
             s.evidencia.pisoAltaDb, s.evidencia.planitud, s.evidencia.duracionMs,
             s.evidencia.vibG, s.evidencia.deltaVibMs);
    r.verificar("2.1", "golpe + estallido 250 ms + vibracion -> ROTURA_VENTANA",
                s.decision == DecisionRotura::ROTURA_VENTANA, detalle);
    r.verificar("2.1", "evidencia: planitud >= 0.3 (ruido de banda ancha)",
                s.evidencia.planitud >= u.planitudMin, "");
    r.verificar("2.1", "evidencia: duracion >= 150 ms",
                s.evidencia.duracionMs >= u.estallidoMinMs, "");
    r.verificar("2.1", "evidencia: vibracion fusionada con delta negativo",
                s.evidencia.hayVib && s.evidencia.deltaVibMs < 0, "");
  }

  // --- Caso 2.2: el mismo estallido SIN vibracion -> VIDRIO_ROTO_EXTERNO ---
  {
    GuionRotura g;
    g.tsGolpeMs = 2000; g.duracionEstallidoMs = 250;
    const ResultadoRotura s = simularRotura(g, u);

    snprintf(detalle, sizeof(detalle), "dur=%u ms  plan=%.2f",
             s.evidencia.duracionMs, s.evidencia.planitud);
    r.verificar("2.2", "golpe + estallido 250 ms SIN vibracion -> VIDRIO_ROTO_EXTERNO",
                s.decision == DecisionRotura::VIDRIO_ROTO_EXTERNO, detalle);
  }

  // --- Caso 2.3: estallido de 80 ms (menos de 150 ms) -> nada ---
  {
    GuionRotura g;
    g.tsGolpeMs = 2000; g.duracionEstallidoMs = 80;
    g.tsVibracionMs = 2000; g.vibracionG = 0.31f;
    const ResultadoRotura s = simularRotura(g, u);

    snprintf(detalle, sizeof(detalle), "decision=%d", (int)s.decision);
    r.verificar("2.3", "estallido de solo 80 ms -> ningun evento de rotura",
                s.decision != DecisionRotura::ROTURA_VENTANA &&
                s.decision != DecisionRotura::VIDRIO_ROTO_EXTERNO, detalle);
  }

  // --- Caso 2.4: tono puro de 5 kHz (planitud baja) -> nada ---
  {
    GuionRotura g;
    g.tsGolpeMs = 2000; g.duracionEstallidoMs = 300; g.estallidoTono = true;
    const ResultadoRotura s = simularRotura(g, u);

    snprintf(detalle, sizeof(detalle), "decision=%d", (int)s.decision);
    r.verificar("2.4", "tono puro de 5 kHz en la banda alta -> ningun evento",
                s.decision == DecisionRotura::NINGUNA, detalle);
  }

  // --- Caso 2.5: golpe grave solo (sin estallido) -> se descarta sin evento ---
  {
    GuionRotura g;
    g.tsGolpeMs = 2000; g.duracionEstallidoMs = 0;
    const ResultadoRotura s = simularRotura(g, u);
    r.verificar("2.5", "golpe SOLO acustico sin estallido -> se descarta sin evento",
                s.decision == DecisionRotura::NINGUNA, "");
  }

  // --- Caso 2.6: vibracion sola -> GOLPE_MANIPULACION al cumplirse 1 s ---
  {
    GuionRotura g;
    g.tsGolpeMs = -1;                 // sin golpe acustico
    g.duracionEstallidoMs = 0;
    g.tsVibracionMs = 2000; g.vibracionG = 0.42f;
    const ResultadoRotura s = simularRotura(g, u);

    snprintf(detalle, sizeof(detalle), "ts_evento=%lld ms  vib=%.2f g",
             (long long)s.tsEventoRelMs, s.evidencia.vibG);
    r.verificar("2.6", "vibracion 0.42 g sin estallido -> GOLPE_MANIPULACION",
                s.decision == DecisionRotura::GOLPE_MANIPULACION, detalle);
    r.verificar("2.6", "el evento se fecha en el instante de la vibracion (t_golpe)",
                s.tsEventoRelMs == 2000, "");
    r.verificar("2.6", "un solo evento (antirrebote de 15 s)", s.decisiones == 1, "");
  }

  // --- Caso 2.7: ruido de fondo sin eventos -> nada ---
  {
    GuionRotura g;
    g.tsGolpeMs = -1; g.duracionEstallidoMs = 0;
    const ResultadoRotura s = simularRotura(g, u);
    snprintf(detalle, sizeof(detalle), "piso_baja=%.1f dB  piso_alta=%.1f dB",
             s.pisoBajaDb, s.pisoAltaDb);
    r.verificar("2.7", "solo ruido de fondo -> ningun evento (sin falsos positivos)",
                s.decision == DecisionRotura::NINGUNA, detalle);
  }
}

// ============================================================================
//  GRUPO 3 — MaquinaModo
// ============================================================================
static void grupo3Modo(Reporte& r) {
  r.titulo("GRUPO 3 - MaquinaModo (EN_MARCHA <-> ESTACIONADO)");
  const UmbralesDeteccion u;
  char detalle[160];

  // --- Caso 3.1: arranca en EN_MARCHA ---
  {
    MaquinaModo m(u);
    r.verificar("3.1", "arranca en EN_MARCHA", m.modo() == Modo::EN_MARCHA, textoModo(m.modo()));
  }

  // --- Caso 3.2: 300 s quieto -> ESTACIONADO ---
  {
    MaquinaModo m(u);
    int cambios = 0;
    for (uint32_t s = 1; s <= u.quietoS; s++) {
      if (m.ventana(0.01f, (int64_t)s * 1000)) cambios++;
      if (s == u.quietoS - 1) {
        r.verificar("3.2", "a los 299 s quieto sigue en EN_MARCHA",
                    m.modo() == Modo::EN_MARCHA, "");
      }
    }
    snprintf(detalle, sizeof(detalle), "cambios=%d  fuente=%s", cambios, textoFuenteModo(m.fuente()));
    r.verificar("3.2", "300 s con variacion < 0.05 g -> ESTACIONADO",
                m.modo() == Modo::ESTACIONADO && cambios == 1, detalle);
    r.verificar("3.2", "fuente = INERCIAL", m.fuente() == FuenteModo::INERCIAL, "");
  }

  // --- Caso 3.3: 10 s de movimiento -> EN_MARCHA ---
  {
    MaquinaModo m(u);
    for (uint32_t s = 1; s <= u.quietoS; s++) m.ventana(0.01f, (int64_t)s * 1000);
    int cambios = 0;
    for (uint32_t s = 1; s <= u.marchaS; s++)
      if (m.ventana(0.20f, (int64_t)(u.quietoS + s) * 1000)) cambios++;

    snprintf(detalle, sizeof(detalle), "cambios=%d", cambios);
    r.verificar("3.3", "10 s con variacion >= 0.05 g -> EN_MARCHA",
                m.modo() == Modo::EN_MARCHA && cambios == 1, detalle);
  }

  // --- Caso 3.4: solo 9 s de movimiento -> sigue ESTACIONADO ---
  {
    MaquinaModo m(u);
    for (uint32_t s = 1; s <= u.quietoS; s++) m.ventana(0.01f, (int64_t)s * 1000);
    for (uint32_t s = 1; s <= u.marchaS - 1; s++) m.ventana(0.20f, (int64_t)(u.quietoS + s) * 1000);

    r.verificar("3.4", "solo 9 s de movimiento -> sigue ESTACIONADO",
                m.modo() == Modo::ESTACIONADO, textoModo(m.modo()));
  }

  // --- Caso 3.5: el contador de movimiento se reinicia si hay un segundo quieto ---
  {
    MaquinaModo m(u);
    for (uint32_t s = 1; s <= u.quietoS; s++) m.ventana(0.01f, (int64_t)s * 1000);
    int64_t t = (int64_t)u.quietoS * 1000;
    for (uint32_t s = 0; s < 8; s++) m.ventana(0.20f, t += 1000);  // 8 s moviendose
    m.ventana(0.01f, t += 1000);                                   // 1 s quieto: reinicia
    for (uint32_t s = 0; s < 8; s++) m.ventana(0.20f, t += 1000);  // otros 8 s

    r.verificar("3.5", "8 s + pausa + 8 s de movimiento -> sigue ESTACIONADO",
                m.modo() == Modo::ESTACIONADO, textoModo(m.modo()));
  }

  // --- Caso 3.6: senal de contacto con antirrebote de 2 s, con prioridad ---
  {
    MaquinaModo m(u, /*usarContacto=*/true);
    m.senalContacto(false, 0);                 // contacto apagado: arranca el antirrebote
    r.verificar("3.6", "antes de los 2 s de antirrebote no cambia de modo",
                m.senalContacto(false, 1500) == false && m.modo() == Modo::EN_MARCHA, "");

    const bool cambio = m.senalContacto(false, 2100);
    snprintf(detalle, sizeof(detalle), "fuente=%s", textoFuenteModo(m.fuente()));
    r.verificar("3.6", "contacto apagado 2 s -> ESTACIONADO con fuente CONTACTO",
                cambio && m.modo() == Modo::ESTACIONADO && m.fuente() == FuenteModo::CONTACTO,
                detalle);

    // Con el contacto estable, la regla inercial ya no manda.
    bool cambioInercial = false;
    for (uint32_t s = 1; s <= u.marchaS + 5; s++)
      if (m.ventana(0.50f, (int64_t)(3000 + s * 1000))) cambioInercial = true;
    r.verificar("3.6", "la senal de contacto tiene prioridad sobre la regla inercial",
                !cambioInercial && m.modo() == Modo::ESTACIONADO, "");

    m.senalContacto(true, 10000);
    r.verificar("3.6", "contacto encendido 2 s -> EN_MARCHA",
                m.senalContacto(true, 12100) && m.modo() == Modo::EN_MARCHA, "");
  }

  // --- Caso 3.7: forzar_modo (comando MQTT de pruebas) ---
  {
    MaquinaModo m(u);
    const bool cambio = m.forzarModo(Modo::ESTACIONADO);
    r.verificar("3.7", "forzar_modo ESTACIONADO funciona y marca fuente FORZADO",
                cambio && m.modo() == Modo::ESTACIONADO && m.fuente() == FuenteModo::FORZADO, "");
  }
}

// ============================================================================
//  GRUPO 4 — EstimadorPiso
// ============================================================================
static void grupo4Piso(Reporte& r) {
  r.titulo("GRUPO 4 - EstimadorPiso (piso de ruido autoaprendido)");
  char detalle[200];

  GeneradorSenal gen;
  AnalizadorEspectral analizador;
  EstimadorPiso piso;

  float bloque[audio_cfg::FFT_PUNTOS];
  const size_t n = audio_cfg::FFT_PUNTOS;
  int64_t ts = 0;

  // --- Caso 4.1: necesita 2 s antes de declararse listo ---
  const size_t bloques2s = 2000 / audio_cfg::BLOQUE_MS;
  for (size_t i = 0; i < bloques2s - 1; i++, ts += audio_cfg::BLOQUE_MS) {
    gen.ruidoBlanco(bloque, n, -62.0f);
    piso.actualizar(analizador.analizar(bloque, n, ts).eAltaDb, false);
  }
  r.verificar("4.1", "no esta listo antes de los 2 s de inicializacion", !piso.listo(), "");

  for (size_t i = 0; i < 10; i++, ts += audio_cfg::BLOQUE_MS) {
    gen.ruidoBlanco(bloque, n, -62.0f);
    piso.actualizar(analizador.analizar(bloque, n, ts).eAltaDb, false);
  }
  const float pisoInicial = piso.valorDb();
  snprintf(detalle, sizeof(detalle), "piso=%.2f dB", pisoInicial);
  r.verificar("4.1", "pasados los 2 s queda listo con el piso del ambiente",
              piso.listo(), detalle);

  // --- Caso 4.2: se adapta a un ruido de fondo nuevo (rampa de +15 dB en 60 s) ---
  //  La regla de aprendizaje solo acepta bloques por debajo de piso + 6 dB, asi
  //  que sigue cambios graduales del ambiente (lo real) pero nunca un evento.
  const float dbfsFinal = -47.0f;
  const size_t bloquesRampa = 60000 / audio_cfg::BLOQUE_MS;
  for (size_t i = 0; i < bloquesRampa; i++, ts += audio_cfg::BLOQUE_MS) {
    const float dbfs = -62.0f + 15.0f * ((float)i / (float)bloquesRampa);
    gen.ruidoBlanco(bloque, n, dbfs);
    piso.actualizar(analizador.analizar(bloque, n, ts).eAltaDb, false);
  }
  // Referencia: energia de banda del ruido de fondo final, medida directamente.
  gen.ruidoBlanco(bloque, n, dbfsFinal);
  const float referenciaFinal = analizador.analizar(bloque, n, ts).eAltaDb;
  const float pisoAdaptado = piso.valorDb();

  snprintf(detalle, sizeof(detalle), "piso=%.2f dB  referencia=%.2f dB  subio %.2f dB",
           pisoAdaptado, referenciaFinal, pisoAdaptado - pisoInicial);
  r.verificar("4.2", "el piso sube con el ambiente (adaptacion > 8 dB)",
              pisoAdaptado - pisoInicial > 8.0f, detalle);
  r.verificar("4.2", "el piso queda a menos de 5 dB del ruido de fondo real",
              fabsf(pisoAdaptado - referenciaFinal) < 5.0f, "");

  // --- Caso 4.3: NO aprende un estallido (bloques muy por encima del piso) ---
  const float antesDelEstallido = piso.valorDb();
  for (size_t i = 0; i < 40; i++, ts += audio_cfg::BLOQUE_MS) {   // 640 ms de estallido
    gen.ruidoBanda(bloque, n, 3000.0f, 7000.0f, -20.0f);
    piso.actualizar(analizador.analizar(bloque, n, ts).eAltaDb, false);
  }
  const float despuesDelEstallido = piso.valorDb();
  snprintf(detalle, sizeof(detalle), "antes=%.2f dB  despues=%.2f dB  delta=%.3f dB",
           antesDelEstallido, despuesDelEstallido, despuesDelEstallido - antesDelEstallido);
  r.verificar("4.3", "640 ms de estallido NO mueven el piso (< 1 dB)",
              fabsf(despuesDelEstallido - antesDelEstallido) < 1.0f, detalle);

  // --- Caso 4.4: tampoco aprende mientras hay una deteccion en curso ---
  const float antesDeteccion = piso.valorDb();
  for (size_t i = 0; i < 100; i++, ts += audio_cfg::BLOQUE_MS) {
    gen.ruidoBlanco(bloque, n, -75.0f);   // ambiente mucho mas silencioso
    piso.actualizar(analizador.analizar(bloque, n, ts).eAltaDb, /*deteccionEnCurso=*/true);
  }
  r.verificar("4.4", "con una deteccion en curso el piso queda congelado",
              fabsf(piso.valorDb() - antesDeteccion) < 0.001f, "");

  // --- Caso 4.5: recalibrar_piso vuelve a empezar ---
  piso.recalibrar();
  r.verificar("4.5", "recalibrar() deja el piso en estado no listo", !piso.listo(), "");
}

// ============================================================================
//  GRUPO 5 — AnalizadorEspectral (sanidad del analisis de audio)
// ============================================================================
static void grupo5Analizador(Reporte& r) {
  r.titulo("GRUPO 5 - AnalizadorEspectral (FFT 256 + Hann, 62.5 Hz/bin)");
  char detalle[200];

  GeneradorSenal gen;
  AnalizadorEspectral analizador;
  float bloque[audio_cfg::FFT_PUNTOS];
  const size_t n = audio_cfg::FFT_PUNTOS;

  // --- Caso 5.1: dB SPL = 120 + dBFS_pico ---
  {
    gen.reiniciarTiempo();
    gen.tono(bloque, n, 1000.0f, -8.0f, -120.0f);   // -8 dBFS -> ~112 dB SPL
    const ResultadoBloque b = analizador.analizar(bloque, n, 0);
    snprintf(detalle, sizeof(detalle), "dbfs_pico=%.2f  spl=%.2f dB", b.dbfsPico, b.splDb);
    r.verificar("5.1", "un tono de -8 dBFS da ~112 dB SPL",
                b.splDb > 110.0f && b.splDb < 114.0f, detalle);
  }

  // --- Caso 5.2: un golpe de 200 Hz sube la banda baja, no la alta ---
  {
    gen.reiniciarTiempo();
    gen.tono(bloque, n, 200.0f, -22.0f);
    const ResultadoBloque b = analizador.analizar(bloque, n, 0);
    snprintf(detalle, sizeof(detalle), "e_baja=%.1f dB  e_alta=%.1f dB", b.eBajaDb, b.eAltaDb);
    r.verificar("5.2", "tono de 200 Hz: banda baja muy por encima de la alta",
                b.eBajaDb > b.eAltaDb + 30.0f, detalle);
  }

  // --- Caso 5.3: ruido de banda 3-7 kHz -> planitud alta ---
  {
    gen.reiniciarTiempo();
    gen.ruidoBanda(bloque, n, 3000.0f, 7000.0f, -26.0f);
    const ResultadoBloque b = analizador.analizar(bloque, n, 0);
    snprintf(detalle, sizeof(detalle), "planitud=%.3f  e_alta=%.1f dB", b.planitud, b.eAltaDb);
    r.verificar("5.3", "ruido de banda ancha 3-7 kHz: planitud >= 0.3",
                b.planitud >= 0.3f, detalle);
  }

  // --- Caso 5.4: tono puro de 5 kHz -> planitud baja ---
  {
    gen.reiniciarTiempo();
    gen.tono(bloque, n, 5000.0f, -26.0f);
    const ResultadoBloque b = analizador.analizar(bloque, n, 0);
    snprintf(detalle, sizeof(detalle), "planitud=%.4f", b.planitud);
    r.verificar("5.4", "tono puro de 5 kHz: planitud < 0.3 (no es un estallido)",
                b.planitud < 0.3f, detalle);
  }
}

// ============================================================================
//  main
// ============================================================================
int main() {
  printf("==================================================================\n");
  printf(" PRUEBAS DE LAS REGLAS DE DETECCION  (nodo ESP32-S3 v3.0.0)\n");
  printf(" Senales sinteticas a %u Hz, bloques de %u muestras (%u ms)\n",
         audio_cfg::MUESTREO_HZ, (unsigned)audio_cfg::FFT_PUNTOS, audio_cfg::BLOQUE_MS);
  printf("==================================================================\n");

  Reporte reporte;
  grupo1Choque(reporte);
  grupo2Rotura(reporte);
  grupo3Modo(reporte);
  grupo4Piso(reporte);
  grupo5Analizador(reporte);
  return reporte.resumen();
}
