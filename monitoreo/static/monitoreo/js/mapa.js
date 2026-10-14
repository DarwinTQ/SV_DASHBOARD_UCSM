/**
 * MapaVehicular: abstrae Google Maps JS (si hay clave) o Leaflet/OSM (respaldo).
 * Solo se usa el modo Leaflet si no se cargó el script de Google Maps.
 */
class MapaVehicular {
  constructor({ elementoId, zoom = 16 }) {
    this.elementoId = elementoId;
    this.zoom = zoom;
    this.usaGoogle = typeof google !== "undefined" && typeof google.maps !== "undefined";
    this._mapa = null;
    this._marcadores = [];
    this._inicializar();
  }

  _inicializar() {
    const elemento = document.getElementById(this.elementoId);
    if (!elemento) return;

    if (this.usaGoogle) {
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
