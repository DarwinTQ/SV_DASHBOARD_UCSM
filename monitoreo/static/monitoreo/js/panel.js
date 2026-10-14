/**
 * PanelTiempoReal: conecta el WebSocket del panel, actualiza KPIs, estado de
 * dispositivos, cámara en vivo, gráfica de telemetría, mapa y feed de
 * incidencias conforme llegan los mensajes (sección 10).
 */
class PanelTiempoReal {
  constructor({ urlWebSocket, urlEstado, googleMapsApiKey }) {
    this.urlWebSocket = urlWebSocket;
    this.urlEstado = urlEstado;
    this.googleMapsApiKey = googleMapsApiKey;
    this._grafica = null;
    this._mapa = null;
    this._notificacionesPermitidas = "Notification" in window && Notification.permission === "granted";
  }

  iniciar() {
    this._inicializarGrafica();
    this._mapa = new MapaVehicular({ elementoId: "mapa-panel" });

    this._conexion = new ConexionTiempoReal({
      url: this.urlWebSocket,
      urlSondeo: this.urlEstado,
      onEstadoConexion: (estado) => this._actualizarIndicadorConexion(estado),
      onMensaje: (mensaje) => this._procesarMensaje(mensaje),
    });
    this._conexion.iniciar();
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
      case "incidencia_nueva": return this._agregarIncidencia(mensaje.data);
      case "incidencia_actualizada": return; // el detalle se refresca al abrir la incidencia
      case "dispositivo_estado": return this._actualizarEstadoDispositivo(mensaje.data);
      case "frame": return this._actualizarFrame(mensaje.data);
      case "sondeo_estado": return this._procesarSondeo(mensaje.data);
    }
  }

  _procesarSondeo(datos) {
    (datos.dispositivos || []).forEach((d) => {
      this._actualizarEstadoDispositivo({ codigo: d.codigo, en_linea: d.en_linea, armado: d.armado });
      if (d.ultimo_frame_url) this._actualizarFrame({ codigo: d.codigo, url: d.ultimo_frame_url, fps: d.fps_vision, personas: d.personas_detectadas });
    });
  }

  _actualizarEstadoDispositivo(datos) {
    const fila = document.querySelector(`[data-dispositivo="${datos.codigo}"]`);
    if (!fila) return;
    const badge = fila.querySelector('[data-campo="armado"]');
    if (badge && datos.armado !== undefined) badge.textContent = datos.armado ? "Armado" : "Desarmado";
    if (datos.en_linea === false) fila.classList.add("dispositivo-offline");
    if (datos.en_linea === true) fila.classList.remove("dispositivo-offline");
  }

  _actualizarFrame(datos) {
    const imagen = document.getElementById("frame-camara");
    const sinDatos = document.getElementById("camara-sin-datos");
    if (imagen && datos.url) {
      imagen.src = datos.url;
      imagen.classList.remove("d-none");
      if (sinDatos) sinDatos.classList.add("d-none");
    }
    const fps = document.getElementById("camara-fps");
    const personas = document.getElementById("camara-personas");
    const latencia = document.getElementById("camara-latencia");
    if (fps) fps.textContent = datos.fps != null ? datos.fps : "—";
    if (personas) personas.textContent = datos.personas != null ? datos.personas : "—";
    if (latencia) latencia.textContent = datos.latencia_captura_ms != null ? datos.latencia_captura_ms : "—";
  }

  _inicializarGrafica() {
    const lienzo = document.getElementById("grafica-telemetria");
    if (!lienzo || typeof Chart === "undefined") return;
    this._grafica = new Chart(lienzo, {
      type: "line",
      data: {
        labels: [],
        datasets: [
          { label: "Vibración pico-pico (g)", data: [], borderColor: "#0cb967", backgroundColor: "transparent", tension: .3 },
          { label: "Nivel sonoro (dB SPL)", data: [], borderColor: "#07854a", backgroundColor: "transparent", tension: .3, borderDash: [6, 4] },
        ],
      },
      options: {
        animation: false,
        responsive: true,
        scales: { x: { display: false } },
        plugins: { legend: { labels: { color: "#0a3d24" } } },
      },
    });
  }

  _actualizarTelemetria(datos) {
    if (!this._grafica) return;
    const ahora = new Date().toLocaleTimeString("es-PE", { hour12: false });
    const vib = datos.data?.imu?.vib_pp_g ?? null;
    const spl = datos.data?.audio?.spl_db ?? null;

    this._grafica.data.labels.push(ahora);
    this._grafica.data.datasets[0].data.push(vib);
    this._grafica.data.datasets[1].data.push(spl);
    if (this._grafica.data.labels.length > 60) {
      this._grafica.data.labels.shift();
      this._grafica.data.datasets.forEach((d) => d.data.shift());
    }
    this._grafica.update();

    const gps = datos.data?.gps;
    if (gps && gps.fix && this._mapa) {
      this._mapa.centrar(gps.lat, gps.lon);
    }
  }

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

    if (this._mapa && datos.lat != null) {
      this._mapa.agregarMarcador(datos.lat, datos.lon, datos.nombre_tipo);
    }

    window.mostrarToast(`Nueva incidencia: ${datos.nombre_tipo} (${datos.placa})`);
    if (this._notificacionesPermitidas) {
      new Notification("Nueva incidencia — UCSM", { body: `${datos.nombre_tipo} · ${datos.placa}` });
    }
  }
}
