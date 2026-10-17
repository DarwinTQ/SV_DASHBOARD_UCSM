"""/foto [placa] [flash]."""
from __future__ import annotations

import time as _time

from monitoreo.bot.formateador import FormateadorMensajes

from .base import ComandoBase


class ComandoFoto(ComandoBase):
    nombre = "foto"
    descripcion = "Foto en vivo de la cámara, con YOLO"
    permiso = "consultar"

    def ejecutar(self, ctx) -> None:
        con_flash = any(a.lower() == "flash" for a in ctx.args)
        args_placa = [a for a in ctx.args if a.lower() != "flash"]
        placa = args_placa[0] if args_placa else None

        dispositivo = self.resolver_dispositivo_o_responder(ctx, placa)
        if dispositivo is None:
            return
        self.responder_dispositivo(ctx, dispositivo, con_flash=con_flash)

    def responder_dispositivo(self, ctx, dispositivo, con_flash: bool = False) -> None:
        """Con flash se pide una captura real (el stream no enciende el LED);
        sin flash se toma el cuadro más reciente del stream MJPEG, que es
        instantáneo (sección 5.5)."""
        from monitoreo.services.camara_service import FabricaCamara, ProveedorCuadros
        from monitoreo.services.detector_yolo import DetectorYOLO

        ctx.cliente.send_chat_action(ctx.chat_id, "upload_photo")
        inicio_total = _time.monotonic()

        if con_flash:
            captura = FabricaCamara.para(dispositivo.codigo).capturar(con_flash=True)
        else:
            captura = ProveedorCuadros().mas_reciente(dispositivo.codigo)
        if not captura.exito:
            self._responder_con_ultimo_frame(ctx, dispositivo, captura.error)
            return

        resultado = DetectorYOLO.instancia().detectar(captura.jpg)
        total_ms = (_time.monotonic() - inicio_total) * 1000

        imagen = resultado.imagen_anotada_jpg or captura.jpg
        caption = FormateadorMensajes().pie_foto(
            dispositivo.codigo, resultado.num_detecciones, resultado.confianza_max,
            captura.captura_ms, resultado.tiempo_inferencia_ms, total_ms,
            resumen_clases=resultado.resumen_clases,
        )
        teclado = self._teclados.foto(dispositivo.pk)
        ctx.cliente.send_photo(ctx.chat_id, imagen, caption=caption, reply_markup=teclado)

    def _responder_con_ultimo_frame(self, ctx, dispositivo, error) -> None:
        from config import config
        from monitoreo.services.estado_en_vivo import EstadoEnVivo

        lectura = EstadoEnVivo.instancia().obtener(dispositivo.codigo)
        ruta = config.bd.media_root / "live" / f"{dispositivo.codigo}.jpg"
        if lectura and lectura.ultimo_frame_url and ruta.exists():
            ctx.cliente.send_message(
                ctx.chat_id,
                f"⚠️ No se pudo capturar ahora ({error}). Aquí el último frame de la vigilancia continua.",
            )
            ctx.cliente.send_photo(ctx.chat_id, ruta.read_bytes(), caption="")
            return
        ctx.cliente.send_message(ctx.chat_id, f"❌ No se pudo capturar la foto: {error}")
