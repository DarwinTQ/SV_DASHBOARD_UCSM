/**
 * Mapa en vivo del vehículo (sección 5.6).
 *
 * El nodo publica su posición por MQTT a 5 Hz en marcha y el servidor la
 * reenvía por WebSocket como mensajes {tipo:"gps"}. Aquí se dibuja:
 *
 *   · marcador con ícono de flecha ROTADO según `rumbo_deg`;
 *   · movimiento INTERPOLADO entre puntos (~200 ms), para que no dé saltos;
 *   · rastro (polilínea) de los últimos 5 minutos, con botón "Limpiar rastro";
 *   · círculo de precisión aproximado (radio ≈ hdop × 2.5 m);
 *   · botones "Seguir vehículo" y "Abrir en Google Maps";
 *   · marcadores de las incidencias recientes.
 *
 * Si no llega GPS en 5 s el marcador se atenúa y aparece "Sin señal GPS".
 *
 * Funciona con Google Maps JS API (si hay GOOGLE_MAPS_API_KEY) y con
 * Leaflet/OSM como respaldo: toda la diferencia está encapsulada en las dos
 * implementaciones de CapaMapa, el resto del código no sabe cuál se usa.
 */

const VERDE_UCSM = "#0cb967";
const VERDE_OSCURO = "#07854a";
const BLANCO = "#ffffff";
const RASTRO_SEGUNDOS = 300;        // 5 minutos
const INTERPOLACION_MS = 200;       // duración de la animación entre puntos
const SIN_SENAL_MS = 5000;          // 5 s sin GPS -> marcador atenuado
const METROS_POR_HDOP = 2.5;        // radio del círculo de precisión

// ============================================================================
//  CapaMapa — interfaz común a Google Maps y Leaflet
// ============================================================================
class CapaMapa {
  /** @returns {boolean} true si la capa quedó lista para dibujar. */
  iniciar(_elemento, _zoom) { return false; }
  centrar(_lat, _lon) {}
  dibujarVehiculo(_lat, _lon, _rumboDeg, _atenuado) {}
  dibujarPrecision(_lat, _lon, _radioM) {}
  dibujarRastro(_puntos) {}
  limpiarRastro() {}
  agregarIncidencia(_lat, _lon, _etiqueta) {}
  limpiarIncidencias() {}
  ajustarAlRastro(_puntos) {}
}

class CapaGoogleMaps extends CapaMapa {
  iniciar(elemento, zoom) {
    this._mapa = new google.maps.Map(elemento, {
      center: { lat: -16.398866, lng: -71.536961 },
      zoom: zoom,
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: true,
    });
    this._vehiculo = null;
    this._precision = null;
    this._rastro = null;
    this._incidencias = [];
    return true;
  }

  centrar(lat, lon) { this._mapa.panTo({ lat: lat, lng: lon }); }

  /** Flecha vectorial: Google rota el símbolo con `rotation` (0 = norte). */
  dibujarVehiculo(lat, lon, rumboDeg, atenuado) {
    const icono = {
      path: google.maps.SymbolPath.FORWARD_CLOSED_ARROW,
      scale: 6,
      rotation: rumboDeg || 0,
      fillColor: atenuado ? "#9bbfae" : VERDE_UCSM,
      fillOpacity: atenuado ? 0.5 : 1,
      strokeColor: BLANCO,
      strokeWeight: 2,
    };
    if (this._vehiculo === null) {
      this._vehiculo = new google.maps.Marker({
        position: { lat: lat, lng: lon }, map: this._mapa, icon: icono,
        title: "Vehículo", zIndex: 999,
      });
      return;
    }
    this._vehiculo.setPosition({ lat: lat, lng: lon });
    this._vehiculo.setIcon(icono);
  }

  dibujarPrecision(lat, lon, radioM) {
    if (this._precision === null) {
      this._precision = new google.maps.Circle({
        map: this._mapa, center: { lat: lat, lng: lon }, radius: radioM,
        strokeColor: VERDE_UCSM, strokeOpacity: 0.5, strokeWeight: 1,
        fillColor: VERDE_UCSM, fillOpacity: 0.12, clickable: false,
      });
      return;
    }
    this._precision.setCenter({ lat: lat, lng: lon });
    this._precision.setRadius(radioM);
  }

  dibujarRastro(puntos) {
    const camino = puntos.map((p) => ({ lat: p.lat, lng: p.lon }));
    if (this._rastro === null) {
      this._rastro = new google.maps.Polyline({
        map: this._mapa, path: camino, strokeColor: VERDE_OSCURO,
        strokeOpacity: 0.85, strokeWeight: 4,
      });
      return;
    }
    this._rastro.setPath(camino);
  }

  limpiarRastro() { if (this._rastro) this._rastro.setPath([]); }

  agregarIncidencia(lat, lon, etiqueta) {
    const marcador = new google.maps.Marker({
      position: { lat: lat, lng: lon }, map: this._mapa, title: etiqueta || "Incidencia",
      icon: {
        path: google.maps.SymbolPath.CIRCLE, scale: 7,
        fillColor: BLANCO, fillOpacity: 1, strokeColor: VERDE_OSCURO, strokeWeight: 3,
      },
    });
    this._incidencias.push(marcador);
  }

  limpiarIncidencias() {
    this._incidencias.forEach((m) => m.setMap(null));
    this._incidencias = [];
  }

  ajustarAlRastro(puntos) {
    if (!puntos.length) return;
    const limites = new google.maps.LatLngBounds();
    puntos.forEach((p) => limites.extend({ lat: p.lat, lng: p.lon }));
    this._mapa.fitBounds(limites);
  }
}

class CapaLeaflet extends CapaMapa {
  iniciar(elemento, zoom) {
    this._mapa = L.map(elemento).setView([-16.398866, -71.536961], zoom);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      attribution: "&copy; OpenStreetMap",
    }).addTo(this._mapa);
    this._vehiculo = null;
    this._precision = null;
    this._rastro = null;
    this._incidencias = [];
    this._zoom = zoom;
    return true;
  }

  centrar(lat, lon) { this._mapa.panTo([lat, lon]); }

  /** Leaflet no rota marcadores: se usa un divIcon con un SVG y CSS transform. */
  dibujarVehiculo(lat, lon, rumboDeg, atenuado) {
    const color = atenuado ? "#9bbfae" : VERDE_UCSM;
    const html =
      '<div class="marcador-vehiculo" style="transform: rotate(' + (rumboDeg || 0) + 'deg);' +
      (atenuado ? "opacity:.55;" : "") + '">' +
      '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">' +
      '<path d="M12 2 L19 21 L12 17 L5 21 Z" fill="' + color + '" stroke="' + BLANCO +
      '" stroke-width="1.5"/></svg></div>';
    const icono = L.divIcon({
      html: html, className: "marcador-vehiculo-contenedor",
      iconSize: [26, 26], iconAnchor: [13, 13],
    });
    if (this._vehiculo === null) {
      this._vehiculo = L.marker([lat, lon], { icon: icono, zIndexOffset: 999 }).addTo(this._mapa);
      return;
    }
    this._vehiculo.setLatLng([lat, lon]);
    this._vehiculo.setIcon(icono);
  }

  dibujarPrecision(lat, lon, radioM) {
    if (this._precision === null) {
      this._precision = L.circle([lat, lon], {
        radius: radioM, color: VERDE_UCSM, weight: 1, opacity: 0.5,
        fillColor: VERDE_UCSM, fillOpacity: 0.12, interactive: false,
      }).addTo(this._mapa);
      return;
    }
    this._precision.setLatLng([lat, lon]);
    this._precision.setRadius(radioM);
  }

  dibujarRastro(puntos) {
    const camino = puntos.map((p) => [p.lat, p.lon]);
    if (this._rastro === null) {
      this._rastro = L.polyline(camino, {
        color: VERDE_OSCURO, weight: 4, opacity: 0.85,
      }).addTo(this._mapa);
      return;
    }
    this._rastro.setLatLngs(camino);
  }

  limpiarRastro() { if (this._rastro) this._rastro.setLatLngs([]); }

  agregarIncidencia(lat, lon, etiqueta) {
    const marcador = L.circleMarker([lat, lon], {
      radius: 7, color: VERDE_OSCURO, weight: 3, fillColor: BLANCO, fillOpacity: 1,
    }).addTo(this._mapa);
    if (etiqueta) marcador.bindPopup(etiqueta);
    this._incidencias.push(marcador);
  }

  limpiarIncidencias() {
    this._incidencias.forEach((m) => this._mapa.removeLayer(m));
    this._incidencias = [];
  }

  ajustarAlRastro(puntos) {
    if (!puntos.length) return;
    this._mapa.fitBounds(puntos.map((p) => [p.lat, p.lon]));
  }
}

// ============================================================================
//  MapaVehiculoVivo
// ============================================================================
class MapaVehiculoVivo {
  /**
   * @param {object} opciones
   * @param {string} opciones.elementoId    contenedor del mapa
   * @param {string} opciones.codigo        código del dispositivo (ej. "NODO-01")
   * @param {string} [opciones.apiKey]      GOOGLE_MAPS_API_KEY (vacío -> Leaflet)
   * @param {number} [opciones.zoom]
   * @param {string} [opciones.prefijoIds]  prefijo de los ids del panel de datos
   * @param {string} [opciones.urlRastro]   GET del rastro inicial
   * @param {string} [opciones.urlLimpiar]  POST para limpiar el rastro
   */
  constructor({ elementoId, codigo, apiKey = "", zoom = 16, prefijoIds = "mapa",
                urlRastro = "", urlLimpiar = "" }) {
    this.elementoId = elementoId;
    this.codigo = codigo;
    this.apiKey = apiKey;
    this.zoom = zoom;
    this.prefijoIds = prefijoIds;
    this.urlRastro = urlRastro;
    this.urlLimpiar = urlLimpiar;

    this._capa = null;
    this._listo = false;
    this._seguir = true;
    this._rastro = [];              // [{lat, lon, t}]
    this._ultimoPunto = null;       // último dato recibido (sin interpolar)
    this._posicionPintada = null;   // posición realmente dibujada
    this._rumboPintado = 0;
    this._animacion = null;
    this._tUltimoGps = 0;
    this._pendientes = [];          // puntos llegados antes de que el mapa esté listo
    this._incidenciasPendientes = [];
  }

  // ------------------------------------------------------------------ //
  // Arranque
  // ------------------------------------------------------------------ //
  async iniciar() {
    const elemento = document.getElementById(this.elementoId);
    if (!elemento) return;

    this._capa = await this._crearCapa(elemento);
    if (this._capa === null) return;
    this._listo = true;

    this._conectarControles();
    this._pendientes.forEach((p) => this.actualizarPosicion(p));
    this._pendientes = [];
    this._incidenciasPendientes.forEach((i) => this.agregarIncidencia(i.lat, i.lon, i.etiqueta));
    this._incidenciasPendientes = [];

    await this._cargarRastroInicial();
    // Vigila la antigüedad del dato: si deja de llegar GPS, atenúa el marcador.
    this._temporizadorSenal = setInterval(() => this._refrescarPanel(), 500);
  }

  async _crearCapa(elemento) {
    if (this.apiKey) {
      const disponible = await MapaVehicular.cargarGoogleMaps(this.apiKey);
      if (disponible) return this._iniciarCapa(new CapaGoogleMaps(), elemento);
      MapaVehicular.mostrarAviso(
        this.elementoId,
        "No se pudo cargar Google Maps (sin internet); se usa OpenStreetMap."
      );
    }
    if (typeof L === "undefined") {
      MapaVehicular.mostrarAviso(this.elementoId, "No hay ningún proveedor de mapas disponible.");
      return null;
    }
    return this._iniciarCapa(new CapaLeaflet(), elemento);
  }

  _iniciarCapa(capa, elemento) {
    elemento.innerHTML = "";
    return capa.iniciar(elemento, this.zoom) ? capa : null;
  }

  _id(sufijo) { return this.prefijoIds + "-" + sufijo; }

  _texto(sufijo, valor) {
    const elemento = document.getElementById(this._id(sufijo));
    if (elemento) elemento.textContent = valor;
  }

  _conectarControles() {
    const botonSeguir = document.getElementById(this._id("seguir"));
    if (botonSeguir) {
      botonSeguir.addEventListener("click", () => {
        this._seguir = !this._seguir;
        botonSeguir.classList.toggle("activo", this._seguir);
        botonSeguir.setAttribute("aria-pressed", String(this._seguir));
        if (this._seguir && this._ultimoPunto) {
          this._capa.centrar(this._ultimoPunto.lat, this._ultimoPunto.lon);
        }
      });
      botonSeguir.classList.toggle("activo", this._seguir);
      botonSeguir.setAttribute("aria-pressed", String(this._seguir));
    }

    const botonLimpiar = document.getElementById(this._id("limpiar"));
    if (botonLimpiar) botonLimpiar.addEventListener("click", () => this.limpiarRastro());

    const botonAjustar = document.getElementById(this._id("ajustar"));
    if (botonAjustar) botonAjustar.addEventListener("click", () => this._capa.ajustarAlRastro(this._rastro));
  }

  /** Rastro ya acumulado en memoria del servidor, para no partir de un mapa vacío. */
  async _cargarRastroInicial() {
    if (!this.urlRastro) return;
    try {
      const resp = await fetch(this.urlRastro, { credentials: "same-origin" });
      if (!resp.ok) return;
      const datos = await resp.json();
      (datos.rastro || []).forEach((p) => this._rastro.push({ lat: p.lat, lon: p.lon, t: Date.now() }));
      this._podarRastro();
      if (this._rastro.length) this._capa.dibujarRastro(this._rastro);
      if (datos.ultimo) this.actualizarPosicion(datos.ultimo);
    } catch (error) {
      // Sin rastro previo no pasa nada: se irá llenando con el WebSocket.
    }
  }

  // ------------------------------------------------------------------ //
  // Entrada de datos
  // ------------------------------------------------------------------ //
  /** Llamar con el `data` de cada mensaje WebSocket {tipo:"gps"}. */
  actualizarPosicion(punto) {
    if (!punto) return;
    if (punto.fix === false) { this._refrescarPanel(punto); return; }
    if (punto.lat == null || punto.lon == null) return;

    if (!this._listo) { this._pendientes.push(punto); return; }

    this._tUltimoGps = Date.now();
    const anterior = this._posicionPintada || { lat: punto.lat, lon: punto.lon };
    this._ultimoPunto = punto;

    this._rastro.push({ lat: punto.lat, lon: punto.lon, t: Date.now() });
    this._podarRastro();
    this._capa.dibujarRastro(this._rastro);

    this._animarHacia(anterior, punto);
    this._refrescarPanel(punto);
  }

  agregarIncidencia(lat, lon, etiqueta) {
    if (lat == null || lon == null) return;
    if (!this._listo) { this._incidenciasPendientes.push({ lat, lon, etiqueta }); return; }
    this._capa.agregarIncidencia(lat, lon, etiqueta);
  }

  limpiarRastro() {
    this._rastro = [];
    if (this._listo) this._capa.limpiarRastro();
    if (!this.urlLimpiar) return;
    // También se vacía en el servidor, para que al recargar no reaparezca.
    fetch(this.urlLimpiar, {
      method: "POST", credentials: "same-origin",
      headers: { "X-CSRFToken": window.obtenerCookie ? window.obtenerCookie("csrftoken") : "" },
    }).catch(() => {});
  }

  detener() {
    clearInterval(this._temporizadorSenal);
    if (this._animacion) cancelAnimationFrame(this._animacion);
  }

  // ------------------------------------------------------------------ //
  // Dibujo
  // ------------------------------------------------------------------ //
  _podarRastro() {
    const limite = Date.now() - RASTRO_SEGUNDOS * 1000;
    while (this._rastro.length && this._rastro[0].t < limite) this._rastro.shift();
  }

  /**
   * Interpola la posición y el rumbo entre el punto anterior y el nuevo durante
   * INTERPOLACION_MS. Sin esto, a 5 Hz el marcador "salta" cada 200 ms.
   */
  _animarHacia(desde, hasta) {
    if (this._animacion) cancelAnimationFrame(this._animacion);
    const rumboDesde = this._rumboPintado;
    const rumboHasta = hasta.rumbo_deg != null ? hasta.rumbo_deg : rumboDesde;
    const inicio = performance.now();

    const paso = (ahora) => {
      const avance = Math.min(1, (ahora - inicio) / INTERPOLACION_MS);
      const lat = desde.lat + (hasta.lat - desde.lat) * avance;
      const lon = desde.lon + (hasta.lon - desde.lon) * avance;
      const rumbo = this._interpolarAngulo(rumboDesde, rumboHasta, avance);

      this._posicionPintada = { lat: lat, lon: lon };
      this._rumboPintado = rumbo;
      this._capa.dibujarVehiculo(lat, lon, rumbo, this._sinSenal());
      this._capa.dibujarPrecision(lat, lon, this._radioPrecision(hasta.hdop));
      if (this._seguir) this._capa.centrar(lat, lon);

      if (avance < 1) this._animacion = requestAnimationFrame(paso);
      else this._animacion = null;
    };
    this._animacion = requestAnimationFrame(paso);
  }

  /** Interpola por el camino corto: de 350° a 10° gira 20°, no 340°. */
  _interpolarAngulo(desde, hasta, avance) {
    let diferencia = ((hasta - desde + 540) % 360) - 180;
    return (desde + diferencia * avance + 360) % 360;
  }

  _radioPrecision(hdop) {
    const valor = hdop == null ? 2.0 : hdop;
    return Math.max(3, valor * METROS_POR_HDOP);
  }

  _sinSenal() {
    return this._tUltimoGps === 0 || Date.now() - this._tUltimoGps > SIN_SENAL_MS;
  }

  // ------------------------------------------------------------------ //
  // Panel de datos
  // ------------------------------------------------------------------ //
  _refrescarPanel(punto) {
    const dato = punto || this._ultimoPunto;
    const aviso = document.getElementById(this._id("aviso"));
    const sinSenal = this._sinSenal();

    if (aviso) {
      aviso.textContent = sinSenal ? "Sin señal GPS" : "";
      aviso.classList.toggle("d-none", !sinSenal);
    }
    // Repinta el marcador atenuado/normal aunque no llegue un punto nuevo.
    if (this._listo && this._posicionPintada) {
      this._capa.dibujarVehiculo(
        this._posicionPintada.lat, this._posicionPintada.lon, this._rumboPintado, sinSenal,
      );
    }

    if (!dato) return;

    if (dato.fix === false) {
      this._texto("coordenadas", "—");
      this._texto("sats", dato.sats != null ? dato.sats : "—");
      this._texto("hdop", dato.motivo === "hdop" ? "descartada (HDOP alto)" : "—");
      return;
    }

    this._texto("coordenadas", dato.lat.toFixed(7) + ", " + dato.lon.toFixed(7));
    this._texto("velocidad", (dato.vel_kmh != null ? dato.vel_kmh : 0) + " km/h");
    this._texto("rumbo", (dato.rumbo_deg != null ? Math.round(dato.rumbo_deg) : 0) + "° " +
                          this._puntoCardinal(dato.rumbo_deg));
    this._texto("sats", dato.sats != null ? dato.sats : "—");
    this._texto("hdop", dato.hdop != null ? dato.hdop : "—");
    this._texto("modo", dato.modo === "EN_MARCHA" ? "EN MARCHA" : (dato.modo === "ESTACIONADO" ? "ESTACIONADO" : "—"));
    this._texto("antiguedad", "hace " + ((Date.now() - this._tUltimoGps) / 1000).toFixed(1) + " s");
    this._texto("puntos", String(this._rastro.length));

    const enlace = document.getElementById(this._id("google"));
    if (enlace) {
      enlace.href = "https://www.google.com/maps/search/?api=1&query=" +
                    dato.lat.toFixed(7) + "," + dato.lon.toFixed(7);
    }
  }

  _puntoCardinal(grados) {
    if (grados == null) return "";
    const nombres = ["N", "NE", "E", "SE", "S", "SO", "O", "NO"];
    return "(" + nombres[Math.round(((grados % 360) / 45)) % 8] + ")";
  }
}
