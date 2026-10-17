"""
Prueba en tiempo real del modelo YOLO26 de la tesis con la cámara web.
Teclas:  q = salir | s = guardar captura | + / - = subir/bajar confianza | p = pausar
"""
import argparse, csv, time
from datetime import datetime
from pathlib import Path

import cv2
import torch
from ultralytics import YOLO

# ------------------------- Configuración -------------------------
MODELO_POR_DEFECTO = r"D:\Tesis_2026\SV_INTERFAZ_UCSM\modelos_ia\yolo26_ucsm_best.pt"
CARPETA_SALIDA     = Path(r"D:\Tesis_2026\SV_INTERFAZ_UCSM\pruebas_camara")
CLASES_ALERTA      = None      # None = todas las clases generan alerta; o p. ej. ["persona", "arma"]
FRAMES_CONFIRMACION = 2        # igual que VISION_FRAMES_CONFIRMACION del sistema
COOLDOWN_S          = 10       # segundos entre alertas
VERDE = (103, 185, 12)         # #0cb967 en BGR


class ProbadorCamara:
    def __init__(self, ruta_modelo, indice_camara, confianza, imgsz):
        self.modelo = YOLO(ruta_modelo)
        self.nombres = self.modelo.names
        self.gpu = torch.cuda.is_available()
        self.device = 0 if self.gpu else "cpu"
        self.confianza = confianza
        self.imgsz = imgsz
        self.camara = cv2.VideoCapture(indice_camara, cv2.CAP_DSHOW)   # DSHOW abre rápido en Windows
        self.camara.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        self.camara.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        if not self.camara.isOpened():
            raise RuntimeError(f"No se pudo abrir la cámara {indice_camara} (prueba --camara 1)")

        CARPETA_SALIDA.mkdir(parents=True, exist_ok=True)
        sello = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.ruta_csv = CARPETA_SALIDA / f"tiempos_{sello}.csv"
        self.csv = open(self.ruta_csv, "w", newline="", encoding="utf-8")
        self.escritor = csv.writer(self.csv)
        self.escritor.writerow(["fecha_hora", "pre_ms", "inferencia_ms", "post_ms", "total_ms",
                                "fps", "detecciones", "clases", "confianza_max", "alerta"])

        self.frames_seguidos = 0
        self.ultima_alerta = 0.0
        self.fps = 0.0
        self.alertas = 0
        print(f"Modelo: {ruta_modelo}\nClases: {self.nombres}\n"
              f"Dispositivo: {'GPU ' + torch.cuda.get_device_name(0) if self.gpu else 'CPU'}")

    def _es_objetivo(self, nombre_clase):
        return CLASES_ALERTA is None or nombre_clase in CLASES_ALERTA

    def _evaluar_alerta(self, objetivos, frame_anotado):
        """Misma lógica del sistema: N frames seguidos + cooldown."""
        self.frames_seguidos = self.frames_seguidos + 1 if objetivos else 0
        ahora = time.time()
        if self.frames_seguidos >= FRAMES_CONFIRMACION and ahora - self.ultima_alerta >= COOLDOWN_S:
            self.ultima_alerta = ahora
            self.alertas += 1
            ruta = CARPETA_SALIDA / f"alerta_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            cv2.imwrite(str(ruta), frame_anotado)
            print(f"🚨 ALERTA #{self.alertas}: {', '.join(objetivos)} → {ruta.name}")
            return True
        return False

    def _dibujar_panel(self, img, r, total_ms, alerta_activa):
        h, w = img.shape[:2]
        cv2.rectangle(img, (0, 0), (w, 70), (255, 255, 255), -1)
        cv2.rectangle(img, (0, 68), (w, 72), VERDE, -1)
        lineas = [
            f"FPS {self.fps:5.1f} | total {total_ms:5.1f} ms | inferencia {r.speed['inference']:5.1f} ms",
            f"conf >= {self.confianza:.2f} | detecciones {len(r.boxes)} | alertas {self.alertas} | "
            f"{'GPU' if self.gpu else 'CPU'}",
        ]
        for i, t in enumerate(lineas):
            cv2.putText(img, t, (10, 25 + i * 28), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (36, 61, 10), 2)
        if alerta_activa:
            cv2.rectangle(img, (0, h - 50), (w, h), VERDE, -1)
            cv2.putText(img, "ALERTA DETECTADA", (10, h - 15),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 255, 255), 3)

    def ejecutar(self):
        pausado = False
        mostrar_alerta_hasta = 0.0
        while True:
            if not pausado:
                t0 = time.perf_counter()
                ok, frame = self.camara.read()
                if not ok:
                    print("No se pudo leer la cámara"); break

                r = self.modelo.predict(frame, imgsz=self.imgsz, conf=self.confianza,
                                        device=self.device, half=self.gpu, verbose=False)[0]
                anotado = r.plot()

                clases = [self.nombres[int(c)] for c in r.boxes.cls] if len(r.boxes) else []
                objetivos = sorted({c for c in clases if self._es_objetivo(c)})
                conf_max = float(r.boxes.conf.max()) if len(r.boxes) else 0.0
                hubo_alerta = self._evaluar_alerta(objetivos, anotado)
                if hubo_alerta:
                    mostrar_alerta_hasta = time.time() + 2

                total_ms = (time.perf_counter() - t0) * 1000
                self.fps = 0.9 * self.fps + 0.1 * (1000 / total_ms) if self.fps else 1000 / total_ms
                self._dibujar_panel(anotado, r, total_ms, time.time() < mostrar_alerta_hasta)

                self.escritor.writerow([datetime.now().isoformat(timespec="milliseconds"),
                                        round(r.speed["preprocess"], 2), round(r.speed["inference"], 2),
                                        round(r.speed["postprocess"], 2), round(total_ms, 2),
                                        round(self.fps, 2), len(r.boxes), "|".join(clases),
                                        round(conf_max, 3), int(hubo_alerta)])
                cv2.imshow("Prueba YOLO26 - UCSM", anotado)

            tecla = cv2.waitKey(1) & 0xFF
            if tecla == ord("q"):
                break
            elif tecla == ord("s"):
                ruta = CARPETA_SALIDA / f"captura_{datetime.now():%Y%m%d_%H%M%S}.jpg"
                cv2.imwrite(str(ruta), anotado); print("Captura guardada:", ruta)
            elif tecla in (ord("+"), ord("=")):
                self.confianza = min(0.95, self.confianza + 0.05)
            elif tecla == ord("-"):
                self.confianza = max(0.05, self.confianza - 0.05)
            elif tecla == ord("p"):
                pausado = not pausado
        self.cerrar()

    def cerrar(self):
        self.camara.release()
        self.csv.close()
        cv2.destroyAllWindows()
        print(f"\nTiempos guardados en: {self.ruta_csv}")
        try:
            import pandas as pd
            df = pd.read_csv(self.ruta_csv)
            if len(df) > 10:
                df = df.iloc[10:]   # descarta los primeros frames (calentamiento)
                print(f"Frames: {len(df)} | inferencia media {df.inferencia_ms.mean():.1f} ms | "
                      f"total medio {df.total_ms.mean():.1f} ms (p95 {df.total_ms.quantile(.95):.1f}) | "
                      f"FPS medio {df.fps.mean():.1f} | alertas {self.alertas}")
        except ImportError:
            pass


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Prueba del modelo YOLO26 con cámara web")
    ap.add_argument("--modelo", default=MODELO_POR_DEFECTO)
    ap.add_argument("--camara", type=int, default=0, help="0 = cámara integrada, 1 = USB externa")
    ap.add_argument("--conf", type=float, default=0.50)
    ap.add_argument("--imgsz", type=int, default=640)
    a = ap.parse_args()
    ProbadorCamara(a.modelo, a.camara, a.conf, a.imgsz).ejecutar()