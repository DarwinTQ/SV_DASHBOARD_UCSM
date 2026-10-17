/**
 * PanelTiempoReal: conecta el WebSocket del panel y reparte cada mensaje.
 *
 * Mensajes que llegan (los emite NotificadorTiempoReal):
 *   telemetria            cada 1 s  -> KPIs y gráfica
 *   gps                   5 Hz      -> MapaVehiculoVivo (sección 5.6)
 *   frame                 ≤5 fps    -> cuadro anotado por YOLO
 *   incidencia_nueva                -> feed + marcador + aviso
 *   incidencia_actualizada          -> (el detalle se refresca al abrirlo)
 *   dispositivo_estado              -> en línea / armado / modo
 *   modo                            -> modo del vehículo
 *   camara_estado                   -> salud de la ESP32-CAM
 *   sondeo_estado                   -> respaldo cuando el WebSocket no conecta
 */
class PanelTiempoReal {
  constructor({ urlWebSocket, urlEstado, googleMapsApiKey, mapaZoom = 16,
                codigoMapa = "", urlRastro = "", urlRastroLimpiar = "" }) {
    this.urlWebSocket = urlWebSocket;
    this.urlEstado = urlEstado;
    this.googleMapsApiKey = googleMapsApiKey;
    this.mapaZoom = mapaZoom;
    this.codigoMapa = codigoMapa;
    this.urlRastro = urlRastro;
    this.urlRastroLimpiar = urlRastroLimpiar;
    this._grafica = null;
    this._mapa = null;
    this._notificacionesPermitidas = "Notification" in window && Notification.permission === "granted";
  }

  iniciar() {
    this._inicializarGrafica();
    this._inicializarMapa();

    this._conexion = new ConexionTiempoReal({
      url: this.urlWebSocket,
      urlSondeo: this.urlEstado,
      onEstadoConexion: (estado) => this._actualizarIndicadorConexion(estado),
      onMensaje: (mensaje) => this._procesarMensaje(mensaje),
    });
    this._conexion.iniciar();
  }

  /**
   * El mapa se monta de forma asíncrona (Google Maps se descarga aparte); hasta
   * que esté listo, MapaVehiculoVivo guarda en cola los puntos que lleguen.
   */
  _inicializarMapa() {
    if (!document.getElementById("mapa-panel")) return;
    this._mapa = new MapaVehiculoVivo({
      elementoId: "mapa-panel",
      codigo: this.codigoMapa,
      apiKey: this.googleMapsApiKey,
      zoom: this.mapaZoom,
      prefijoIds: "mapa-panel",
      urlRastro: this.urlRastro,
      urlLimpiar: this.urlRastroLimpiar,
    });
    this._mapa.iniciar();
  }

  _actualizarIndicadorConexion(estado) {
    const indicador = document.getElementById("indicador-conexion");
    const texto = document.getElementById("texto-conexion");
    if (!indicador) return;
    indicador.classList.remove("conectado", "reconectando");
    indicador.classList.add(estado);
    if (texto) texto.textContent = estado === "conectado" ? "En vivo" : "Reconectando…";
  }

  _procesarMensaje(mensaje) {
    switch (mensaje.tipo) {
      case "telemetria": return this._actualizarTelemetria(mensaje.data);
      case "gps": return this._actualizarGps(mensaje.data);
      case "incidencia_nueva": return this._agregarIncidencia(mensaje.data);
      case "incidencia_actualizada": return; // el detalle se refresca al abrir la incidencia
      case "dispositivo_estado": return this._actualizarEstadoDispositivo(mensaje.data);
      case "modo": return this._actualizarModo(mensaje.data);
      case "camara_estado": return this._actualizarEstadoCamara(mensaje.data);
      case "frame": return this._actualizarFrame(mensaje.data);
      case "sondeo_estado": return this._procesarSondeo(mensaje.data);
    }
  }

  _procesarSondeo(datos) {
    (datos.dispositivos || []).forEach((d) => {
      this._actualizarEstadoDispositivo({
        codigo: d.codigo, en_linea: d.en_linea, armado: d.armado, modo: d.modo,
      });
      if (d.ultimo_frame_url) {
        this._actualizarFrame({
          codigo: d.codigo, url: d.ultimo_frame_url, fps: d.fps_vision,
          personas: d.personas_detectadas,
        });
      }
      if (d.gps && this._esDelMapa(d.codigo)) this._actualizarGps({ codigo: d.codigo, data: d.gps });
    });
  }

  /** El panel muestra un solo mapa: solo interesan los puntos de ese nodo. */
  _esDelMapa(codigo) {
    return !this.codigoMapa || this.codigoMapa === codigo;
  }

  // ------------------------------------------------------------------ //
  // Estado de los dispositivos
  // ------------------------------------------------------------------ //
  _actualizarEstadoDispositivo(datos) {
    const fila = document.querySelector(`[data-dispositivo="${datos.codigo}"]`);
    if (!fila) return;

    const badgeArmado = fila.querySelector('[data-campo="armado"]');
    if (badgeArmado && datos.armado !== undefined) {
      badgeArmado.textContent = datos.armado ? "Armado" : "Desarmado";
    }
    if (datos.modo) this._pintarModo(fila, datos.modo, datos.modo_fuente);
    if (datos.en_linea === false) fila.classList.add("dispositivo-offline");
    if (datos.en_linea === true) fila.classList.remove("dispositivo-offline");
  }

  _actualizarModo(datos) {
    const fila = document.querySelector(`[data-dispositivo="${datos.codigo}"]`);
    if (fila) this._pintarModo(fila, datos.data ? datos.data.modo : datos.modo,
                               datos.data ? datos.data.fuente : datos.modo_fuente);
  }

  _pintarModo(fila, modo, fuente) {
    const badge = fila.querySelector('[data-campo="modo"]');
    if (!badge || !modo) return;
    badge.textContent = modo === "EN_MARCHA" ? "En marcha" : "Estacionado";
    badge.title = fuente ? `Fuente del modo: ${fuente}` : "";
  }

  _actualizarEstadoCamara(datos) {
    const metodo = document.getElementById("camara-metodo");
    const estado = datos.data || datos;
    if (metodo && estado && estado.en_linea === false) metodo.textContent = "cámara fuera de línea";
  }

  // ------------------------------------------------------------------ //
  // Vídeo
  // ------------------------------------------------------------------ //
  _actualizarFrame(datos) {
    const imagen = document.getElementById("frame-camara");
    const sinDatos = document.getElementById("camara-sin-datos");
    if (imagen && datos.url) {
      imagen.src = datos.url;
      imagen.classList.remove("d-none");
      if (sinDatos) sinDatos.classList.add("d-none");
    }
    this._texto("camara-fps", datos.fps != null ? datos.fps : "—");
    this._texto("camara-personas", datos.personas != null ? datos.personas : "—");
    this._texto("camara-latencia", datos.latencia_captura_ms != null
      ? Math.round(datos.latencia_captura_ms) : "—");
    this._texto("camara-metodo", datos.metodo === "stream" ? "stream MJPEG"
      : (datos.metodo || "—"));
  }

  _texto(id, valor) {
    const elemento = document.getElementById(id);
    if (elemento) elemento.textContent = valor;
  }

  // ------------------------------------------------------------------ //
  // GPS y telemetría
  // ------------------------------------------------------------------ //
  _actualizarGps(mensaje) {
    if (!this._mapa || !this._esDelMapa(mensaje.codigo)) return;
    this._mapa.actualizarPosicion(mensaje.data);
  }

  _inicializarGrafica() {
    const lienzo = document.getElementById("grafica-telemetria");
    if (!lienzo || typeof Chart === "undefined") return;
    this._grafica = new Chart(lienzo, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "|a| máx. (g)", data: [], borderColor: "#0cb967", backgroundColor: "transparent", tension: .3, yAxisID: "y" },
          { label: "Pico sonoro (dB SPL)", data: [], borderColor: "#07854a", backgroundColor: "transparent", tension: .3, borderDash: [6, 4], yAxisID: "y1" },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        scales: {
          x: { display: false },
          y: { position: "left", title: { display: true, text: "g" } },
          y1: { position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "dB SPL" } },
        },
        plugins: { legend: { labels: { color: "#0a3d24" } } },
      },
    });
  }

  _actualizarTelemetria(datos) {
    const lectura = datos.data || {};
    if (lectura.modo) {
      const fila = document.querySelector(`[data-dispositivo="${datos.codigo}"]`);
      if (fila) this._pintarModo(fila, lectura.modo, lectura.modo_fuente);
    }
    if (!this._grafica) return;

    const ahora = new Date().toLocaleTimeString("es-PE", { hour12: false });
    // v3.0.0: el MPU llega a ±16 g y el audio reporta el pico del segundo.
    const aceleracion = lectura.imu?.a_max_g ?? null;
    const spl = lectura.audio?.spl_pico_db ?? lectura.audio?.spl_db ?? null;

    this._grafica.data.labels.push(ahora);
    this._grafica.data.datasets[0].data.push(aceleracion);
    this._grafica.data.datasets[1].data.push(spl);
    if (this._grafica.data.labels.length > 60) {
      this._grafica.data.labels.shift();
      this._grafica.data.datasets.forEach((d) => d.data.shift());
    }
    this._grafica.update();
  }

  // ------------------------------------------------------------------ //
  // Incidencias
  // ------------------------------------------------------------------ //
  _agregarIncidencia(datos) {
    const cuerpo = document.getElementById("cuerpo-feed-incidencias");
    if (cuerpo) {
      const fila = document.createElement("tr");
      fila.innerHTML = `
        <td>${datos.miniatura ? `<img src="${datos.miniatura}" class="tabla-miniatura">` : ""}</td>
        <td>${new Date(datos.fecha_hora).toLocaleString("es-PE")}</td>
        <td>${datos.nombre_tipo}</td>
        <td><span class="badge-riesgo badge-riesgo--${datos.riesgo.toLowerCase()}">${datos.riesgo}</span></td>
        <td>${datos.placa}</td>
        <td><a href="/incidencias/${datos.id}/" class="btn btn-sm btn-verde-contorno">Ver</a></td>`;
      cuerpo.prepend(fila);
      while (cuerpo.rows.length > 10) cuerpo.deleteRow(-1);
    }

    if (datos.lat != null && this._mapa) {
      this._mapa.agregarIncidencia(datos.lat, datos.lon, datos.nombre_tipo);
    }

    window.mostrarToast(`Nueva incidencia: ${datos.nombre_tipo} (${datos.placa})`);
    // Los tipos de riesgo BAJO son solo registro: no vale despertar a nadie.
    if (this._notificacionesPermitidas && datos.riesgo !== "BAJO") {
      new Notification("Nueva incidencia — UCSM", { body: `${datos.nombre_tipo} · ${datos.placa}` });
    }
  }
}
