/**
 * Utilidades generales de la interfaz: menú lateral responsive, reloj en
 * vivo, toasts, permiso de notificaciones y el interruptor Armar/Desarmar.
 */
(function () {
  function obtenerCookie(nombre) {
    const valor = document.cookie.split("; ").find((fila) => fila.startsWith(nombre + "="));
    return valor ? decodeURIComponent(valor.split("=")[1]) : null;
  }

  window.mostrarToast = function (mensaje, variante = "info") {
    const contenedor = document.getElementById("contenedor-toasts");
    if (!contenedor) return;
    const toast = document.createElement("div");
    toast.className = "toast-ucsm";
    toast.innerHTML = `<strong>${variante === "error" ? "⚠️ " : "✅ "}</strong> ${mensaje}`;
    contenedor.appendChild(toast);
    setTimeout(() => toast.remove(), 6000);
  };

  function formatearHoraLima(fecha) {
    return new Intl.DateTimeFormat("es-PE", {
      timeZone: "America/Lima", day: "2-digit", month: "2-digit", year: "numeric",
      hour: "2-digit", minute: "2-digit", second: "2-digit",
    }).format(fecha);
  }

  function iniciarRelojVivo() {
    const elemento = document.getElementById("reloj-vivo");
    if (!elemento) return;
    const actualizar = () => { elemento.textContent = formatearHoraLima(new Date()); };
    actualizar();
    setInterval(actualizar, 1000);
  }

  function iniciarSidebarResponsive() {
    const boton = document.getElementById("boton-sidebar");
    const sidebar = document.getElementById("sidebar");
    if (!boton || !sidebar) return;
    boton.addEventListener("click", () => sidebar.classList.toggle("mostrar"));
    document.addEventListener("click", (evento) => {
      if (window.innerWidth >= 992) return;
      if (!sidebar.contains(evento.target) && !boton.contains(evento.target)) {
        sidebar.classList.remove("mostrar");
      }
    });
  }

  function iniciarInterruptoresArmado() {
    document.querySelectorAll(".interruptor-armado").forEach((interruptor) => {
      interruptor.addEventListener("change", async () => {
        const idDispositivo = interruptor.dataset.dispositivoId;
        try {
          const resp = await fetch(`/api/v1/panel/dispositivos/${idDispositivo}/armar/`, {
            method: "POST",
            headers: { "Content-Type": "application/json", "X-CSRFToken": obtenerCookie("csrftoken") },
            body: JSON.stringify({ armado: interruptor.checked }),
          });
          const datos = await resp.json();
          if (!datos.ok) throw new Error(datos.error || "Error desconocido");
          window.mostrarToast(`Dispositivo ${datos.armado ? "armado" : "desarmado"} correctamente.`);
        } catch (error) {
          interruptor.checked = !interruptor.checked;
          window.mostrarToast("No se pudo cambiar el estado del dispositivo.", "error");
        }
      });
    });
  }

  function pedirPermisoNotificaciones() {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission();
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    iniciarRelojVivo();
    iniciarSidebarResponsive();
    iniciarInterruptoresArmado();
    pedirPermisoNotificaciones();
  });

  window.obtenerCookie = obtenerCookie;
})();
