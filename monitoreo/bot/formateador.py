"""Construye los textos HTML que envía el bot (sección 6, hora de Lima)."""
from __future__ import annotations

from django.utils import timezone


def _fh(dt) -> str:
    if dt is None:
        return "—"
    return timezone.localtime(dt).strftime("%d/%m/%Y %H:%M:%S")


def _hace(dt) -> str:
    if dt is None:
        return "sin contacto"
    segundos = (timezone.now() - dt).total_seconds()
    if segundos < 60:
        return f"hace {int(segundos)} s"
    if segundos < 3600:
        return f"hace {int(segundos // 60)} min"
    return f"hace {int(segundos // 3600)} h"


class FormateadorMensajes:
    """Todos los métodos devuelven HTML listo para ``parse_mode=HTML``."""

    def bienvenida(self, vinculado: bool, nombre: str = "") -> str:
        if vinculado:
            saludo = f"👋 Hola{f', {nombre}' if nombre else ''}. " if nombre else "👋 Hola. "
            return (
                f"{saludo}Ya tienes acceso al sistema de monitoreo. Usa el menú de abajo "
                "o escribe /ayuda para ver todos los comandos."
            )
        return (
            "👋 Bienvenido al <b>Sistema de Monitoreo Vehicular UCSM</b>.\n\n"
            "Si eres el <b>propietario</b> de un vehículo registrado, vincúlate con:\n"
            "<code>/vincular DNI TELEFONO</code>\n\n"
            "Si un propietario te invitó como familiar, usa el código que te compartió:\n"
            "<code>/unirse CODIGO</code>"
        )

    def ayuda(self, es_propietario: bool, puede_controlar: bool, vinculado: bool) -> str:
        if not vinculado:
            return (
                "Comandos disponibles:\n"
                "/vincular DNI TELEFONO — vincularte como propietario\n"
                "/unirse CODIGO — vincularte como familiar invitado\n"
                "/ayuda — este mensaje"
            )
        lineas = [
            "📊 /estado [placa] — estado del vehículo",
            "📷 /foto [placa] — foto en vivo con YOLO",
            "📍 /ubicacion [placa] — última ubicación GPS",
            "🛰️ /sensores [placa] — vibración, ruido, inclinación",
            "🔋 /bateria [placa] — batería del nodo",
            "🚨 /ultima [placa] — última incidencia",
            "📋 /incidencias [n] — últimas incidencias",
            "🔔 /alertas on|off — activa/desactiva tus alertas",
            "🔕 /silenciar <minutos> — pausa tus alertas",
            "🚗 /vehiculo — datos del vehículo y dispositivo",
            "📈 /metricas — resumen de resultados de hoy",
        ]
        if puede_controlar:
            lineas.append("🔒 /armar · 🔓 /desarmar — controla la vigilancia")
        if es_propietario:
            lineas += [
                "👪 /invitar <nombre> [parentesco] — invita a un familiar",
                "📜 /familia — lista de accesos del vehículo",
                "🚫 /revocar <nombre> — quita el acceso a alguien",
            ]
        lineas.append("🚪 /salir — quita tu propio acceso")
        return "\n".join(lineas)

    def estado(self, dispositivo, en_linea: bool, lectura, incidencias_hoy: int, ultima) -> str:
        vehiculo = dispositivo.vehiculo
        telemetria = (lectura.ultima_telemetria if lectura else {}) or {}
        bateria = telemetria.get("bateria") or {}
        wifi = telemetria.get("wifi") or {}
        sistema = telemetria.get("sistema") or {}
        camara = (lectura.camara_estado if lectura else {}) or {}

        lineas = [
            f"🚗 <b>{vehiculo.placa}</b> · {vehiculo.marca} {vehiculo.modelo}",
            f"{'🟢 En línea' if en_linea else '🔴 Fuera de línea'} ({_hace(dispositivo.ultimo_contacto)})",
            f"{'🔒 Armado' if dispositivo.armado else '🔓 Desarmado'}",
        ]
        if bateria:
            lineas.append(f"🔋 Batería: {bateria.get('v', '—')} V ({bateria.get('pct', '—')}%)")
        if wifi:
            lineas.append(f"📶 RSSI Wi-Fi: {wifi.get('rssi_dbm', '—')} dBm")
        if sistema:
            lineas.append(f"🌡️ Temperatura: {sistema.get('temp_c', '—')} °C")
        gps = telemetria.get("gps") or {}
        if gps.get("fix"):
            lineas.append(f"📍 GPS: {gps.get('lat')}, {gps.get('lon')} · {gps.get('vel_kmh', 0)} km/h")
        else:
            lineas.append("📍 GPS: sin señal")
        lineas.append(f"📷 Cámara: {'disponible' if camara.get('ok') else 'sin datos'}")
        lineas.append(f"🚨 Incidencias hoy: {incidencias_hoy}")
        if ultima:
            lineas.append(f"Última: {ultima.tipo.nombre} — {_fh(ultima.fecha_hora)}")
        return "\n".join(lineas)

    def sensores(self, lectura) -> str:
        telemetria = (lectura.ultima_telemetria if lectura else {}) or {}
        imu = telemetria.get("imu") or {}
        audio = telemetria.get("audio") or {}
        from config import config

        return "\n".join([
            "🛰️ <b>Sensores</b>",
            f"Vibración pico-pico: {imu.get('vib_pp_g', '—')} g (umbral {config.umbrales.impacto_g} g)",
            f"|a| máx.: {imu.get('a_max_g', '—')} g",
            f"Nivel sonoro: {audio.get('spl_db', '—')} dB SPL (umbral {config.umbrales.ruido_db} dB)",
            f"Inclinación: pitch {imu.get('pitch_deg', '—')}° · roll {imu.get('roll_deg', '—')}° "
            f"(umbral {config.umbrales.inclinacion_deg}°)",
            f"Giro máx.: {imu.get('giro_max_dps', '—')} °/s (umbral {config.umbrales.giro_dps} °/s)",
        ])

    def bateria(self, lectura) -> str:
        telemetria = (lectura.ultima_telemetria if lectura else {}) or {}
        bateria = telemetria.get("bateria") or {}
        return f"🔋 Voltaje: {bateria.get('v', '—')} V\n🔋 Porcentaje: {bateria.get('pct', '—')} %"

    def pie_foto(self, codigo: str, num_personas: int, confianza_max, captura_ms, inferencia_ms, total_ms) -> str:
        lineas = [f"📷 <b>{codigo}</b> · {_fh(timezone.now())}"]
        if confianza_max:
            lineas.append(f"👤 {num_personas} persona(s) · confianza máx. {confianza_max:.0%}")
        else:
            lineas.append("👤 Sin personas detectadas")
        lineas.append(f"⏱️ Captura {captura_ms or 0:.0f} ms · Inferencia {inferencia_ms or 0:.0f} ms · Total {total_ms:.0f} ms")
        return "\n".join(lineas)

    def incidencia_resumen(self, incidencia) -> str:
        validacion = incidencia.get_validacion_display() if incidencia.validacion else "sin validar"
        return (
            f"#{incidencia.pk} · {incidencia.tipo.nombre} · {incidencia.tipo.nivel_riesgo}\n"
            f"🕒 {_fh(incidencia.fecha_hora)} · estado: {incidencia.get_estado_display()} · {validacion}"
        )

    def lista_incidencias(self, incidencias) -> str:
        if not incidencias:
            return "No hay incidencias registradas todavía."
        return "\n\n".join(self.incidencia_resumen(inc) for inc in incidencias)

    def alerta_incidencia(self, incidencia, resultado_yolo=None, evidencia=None,
                          silenciosa: bool = False) -> str:
        """Texto de la alerta que difunde ``DifusorAlertas``.

        Muestra la evidencia resumida de la regla que disparó el evento
        ("6.8 g + 115 dB en 42 ms", "estallido 208 ms, planitud 0.42"), que es
        lo que permite al propietario juzgar la alerta de un vistazo.
        """
        from monitoreo.services.reglas_accion import DescriptorEvidencia

        vehiculo = incidencia.dispositivo.vehiculo
        encabezado = "🔔 <b>AVISO" if silenciosa else "🚨 <b>ALERTA"
        lineas = [
            f"{encabezado}: {incidencia.tipo.nombre}</b> — riesgo {incidencia.tipo.nivel_riesgo}",
            f"🚗 <b>{vehiculo.placa}</b> · {vehiculo.marca} {vehiculo.modelo} · {vehiculo.color}",
        ]
        detalle_tiempo = f"🕒 {_fh(incidencia.fecha_hora)}"
        if resultado_yolo is not None and getattr(resultado_yolo, "confianza_max", None):
            detalle_tiempo += f" · 📈 {resultado_yolo.confianza_max:.0%} · {resultado_yolo.num_personas} persona(s)"
        lineas.append(detalle_tiempo)

        resumen = DescriptorEvidencia.resumen_corto(incidencia.tipo.codigo, evidencia)
        if resumen:
            lineas.append(f"📐 {resumen}")
        if incidencia.observacion:
            lineas.append(f"🔎 {incidencia.observacion.splitlines()[0][:150]}")
        return "\n".join(lineas)[:1024]

    def familia(self, accesos) -> str:
        if not accesos:
            return "Todavía no hay familiares invitados a este vehículo."
        filas = []
        for acceso in accesos:
            estado = "vinculado ✅" if acceso.vinculado else "invitación pendiente ⏳"
            control = "controla ✔" if acceso.puede_controlar else "solo consulta"
            alertas = "alertas ON" if acceso.recibe_alertas else "alertas OFF"
            filas.append(
                f"• <b>{acceso.nombres}</b> ({acceso.get_rol_display()}"
                f"{f', {acceso.parentesco}' if acceso.parentesco else ''}) — {estado} · {control} · {alertas}"
            )
        return "\n".join(filas)

    def vehiculo_info(self, dispositivo) -> str:
        vehiculo = dispositivo.vehiculo
        return "\n".join([
            f"🚗 <b>{vehiculo.placa}</b> · {vehiculo.marca} {vehiculo.modelo} ({vehiculo.anio}) · {vehiculo.color}",
            f"📟 Dispositivo: {dispositivo.codigo} · {dispositivo.get_estado_display()}",
            f"🔧 Firmware: {dispositivo.version_firmware or '—'}",
            f"🕒 Último contacto: {_fh(dispositivo.ultimo_contacto)} ({_hace(dispositivo.ultimo_contacto)})",
        ])

    def metricas_hoy(self, kpis: dict) -> str:
        return "\n".join([
            "📈 <b>Resumen de hoy</b>",
            f"Incidencias: {kpis.get('incidencias_hoy', 0)}",
            f"Alertas enviadas: {kpis.get('alertas_hoy', 0)} "
            f"(éxito {kpis.get('alertas_tasa_exito') if kpis.get('alertas_tasa_exito') is not None else '—'}%)",
            f"Tiempo de respuesta promedio: "
            f"{kpis.get('tiempo_respuesta_promedio_ms') if kpis.get('tiempo_respuesta_promedio_ms') is not None else '—'} ms",
        ])
