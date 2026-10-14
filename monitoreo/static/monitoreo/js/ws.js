/**
 * ConexionTiempoReal: envuelve el WebSocket del panel con reconexión
 * exponencial y, si no logra conectar, recurre a sondeo periódico de una
 * URL de respaldo (GET /api/v1/panel/estado/).
 */
class ConexionTiempoReal {
  constructor({ url, urlSondeo, onMensaje, onEstadoConexion, intervaloSondeoMs = 2000 }) {
    this.url = url;
    this.urlSondeo = urlSondeo;
    this.onMensaje = onMensaje || function () {};
    this.onEstadoConexion = onEstadoConexion || function () {};
    this.intervaloSondeoMs = intervaloSondeoMs;

    this._socket = null;
    this._intentos = 0;
    this._temporizadorReconexion = null;
    this._temporizadorSondeo = null;
    this._usandoSondeo = false;
  }

  iniciar() {
    this._conectar();
  }

  detener() {
    if (this._socket) this._socket.close();
    clearTimeout(this._temporizadorReconexion);
    clearInterval(this._temporizadorSondeo);
  }

  _conectar() {
    this.onEstadoConexion("reconectando");
    try {
      this._socket = new WebSocket(this.url);
    } catch (error) {
      this._programarReconexion();
      return;
    }

    this._socket.onopen = () => {
      this._intentos = 0;
      this._detenerSondeo();
      this.onEstadoConexion("conectado");
    };

    this._socket.onmessage = (evento) => {
      try {
        this.onMensaje(JSON.parse(evento.data));
      } catch (error) {
        console.error("Mensaje WebSocket inválido:", error);
      }
    };

    this._socket.onclose = () => {
      this.onEstadoConexion("reconectando");
      this._programarReconexion();
      this._iniciarSondeo();
    };

    this._socket.onerror = () => {
      if (this._socket) this._socket.close();
    };
  }

  _programarReconexion() {
    this._intentos += 1;
    const espera = Math.min(30000, 1000 * Math.pow(2, this._intentos));
    clearTimeout(this._temporizadorReconexion);
    this._temporizadorReconexion = setTimeout(() => this._conectar(), espera);
  }

  _iniciarSondeo() {
    if (this._usandoSondeo || !this.urlSondeo) return;
    this._usandoSondeo = true;
    this._temporizadorSondeo = setInterval(async () => {
      try {
        const resp = await fetch(this.urlSondeo, { credentials: "same-origin" });
        if (resp.ok) {
          const datos = await resp.json();
          this.onMensaje({ tipo: "sondeo_estado", data: datos });
        }
      } catch (error) {
        // red caída: se reintenta en el siguiente ciclo
      }
    }, this.intervaloSondeoMs);
  }

  _detenerSondeo() {
    this._usandoSondeo = false;
    clearInterval(this._temporizadorSondeo);
  }
}
