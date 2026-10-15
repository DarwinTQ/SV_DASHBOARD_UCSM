/**
 * MapaVehicular: abstrae Google Maps JS (si hay clave) o Leaflet/OSM (respaldo).
 * Toda página que muestre un mapa debe usar `await MapaVehicular.crear(...)`:
 * el constructor directo no descarga el script de Google, así que caería al
 * respaldo Leaflet aunque GOOGLE_MAPS_API_KEY esté configurada.
 */
class MapaVehicular {
  constructor({ elementoId, zoom = 16, forzarGoogle = false }) {
    this.elementoId = elementoId;
    this.zoom = zoom;
    this.usaGoogle = forzarGoogle || MapaVehicular.googleDisponible();
    this._mapa = null;
    this._marcadores = [];
    this._inicializar();
  }

  /** True cuando el script de Google Maps ya está cargado en la página. */
  static googleDisponible() {
    return typeof google !== "undefined" && typeof google.maps !== "undefined";
  }

  /**
   * Carga una sola vez el script de Google Maps con la clave de .env
   * (GOOGLE_MAPS_API_KEY). Resuelve a true si quedó disponible, false si no hay
   * clave o el script no se pudo descargar (p. ej. sin internet en la LAN).
   */
  static cargarGoogleMaps(apiKey) {
    if (MapaVehicular.googleDisponible()) return Promise.resolve(true);
    if (!apiKey) return Promise.resolve(false);
    if (MapaVehicular._promesaGoogle) return MapaVehicular._promesaGoogle;

    MapaVehicular._promesaGoogle = new Promise((resolver) => {
      window.__mapaVehicularGoogleListo = () => resolver(true);
      const script = document.createElement("script");
      script.src =
        "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(apiKey) +
        "&language=es&region=PE&loading=async&callback=__mapaVehicularGoogleListo";
      script.async = true;
      script.onerror = () => resolver(false);
      document.head.appendChild(script);
    });
    return MapaVehicular._promesaGoogle;
  }

  /**
   * Fábrica asíncrona: descarga Google Maps con la clave de .env y devuelve el
   * mapa ya listo. Si hay clave pero el script no se pudo descargar (sin
   * internet en la LAN), muestra el aviso en el contenedor y devuelve null.
   * Sin clave usa el respaldo Leaflet/OSM.
   */
  static async crear({ elementoId, zoom = 16, apiKey = "" }) {
    if (!apiKey) return new MapaVehicular({ elementoId, zoom });

    const disponible = await MapaVehicular.cargarGoogleMaps(apiKey);
    if (!disponible) {
      MapaVehicular.mostrarAviso(
        elementoId, "No se pudo cargar Google Maps (revisa la conexión a internet).",
      );
      return null;
    }
    return new MapaVehicular({ elementoId, zoom, forzarGoogle: true });
  }

  /** Mensaje dentro del contenedor cuando no se pudo mostrar el mapa. */
  static mostrarAviso(elementoId, texto) {
    const elemento = document.getElementById(elementoId);
    if (!elemento) return;
    elemento.innerHTML =
      '<div class="mapa-aviso text-muted small d-flex align-items-center justify-content-center h-100 text-center p-3">' +
      '<span><i class="bi bi-geo-alt"></i> ' + texto + "</span></div>";
  }

  _inicializar() {
    const elemento = document.getElementById(this.elementoId);
    if (!elemento) return;

    if (this.usaGoogle) {
      elemento.dataset.mapaGoogle = "1";
      this._mapa = new google.maps.Map(elemento, {
        center: { lat: -16.398866, lng: -71.536961 }, zoom: this.zoom,
      });
    } else {
      this._mapa = L.map(this.elementoId).setView([-16.398866, -71.536961], this.zoom);
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        attribution: "&copy; OpenStreetMap",
      }).addTo(this._mapa);
    }
  }

  limpiarMarcadores() {
    if (this.usaGoogle) {
      this._marcadores.forEach((m) => m.setMap(null));
    } else {
      this._marcadores.forEach((m) => this._mapa.removeLayer(m));
    }
    this._marcadores = [];
  }

  agregarMarcador(lat, lon, etiqueta) {
    if (!this._mapa || lat == null || lon == null) return;

    if (this.usaGoogle) {
      const marcador = new google.maps.Marker({
        position: { lat, lng: lon }, map: this._mapa, title: etiqueta || "",
      });
      this._marcadores.push(marcador);
    } else {
      const marcador = L.marker([lat, lon]).addTo(this._mapa);
      if (etiqueta) marcador.bindPopup(etiqueta);
      this._marcadores.push(marcador);
    }
  }

  centrar(lat, lon) {
    if (!this._mapa || lat == null || lon == null) return;
    if (this.usaGoogle) {
      this._mapa.setCenter({ lat, lng: lon });
    } else {
      this._mapa.setView([lat, lon], this.zoom);
    }
  }
}

MapaVehicular._promesaGoogle = null;

/** Google Maps llama a este hook si rechaza la clave (facturación, restricciones, etc.). */
window.gm_authFailure = function () {
  console.error("Google Maps rechazó GOOGLE_MAPS_API_KEY (gm_authFailure).");
  document.querySelectorAll("[data-mapa-google]").forEach((elemento) => {
    MapaVehicular.mostrarAviso(elemento.id, "Google Maps rechazó la clave GOOGLE_MAPS_API_KEY.");
  });
};
