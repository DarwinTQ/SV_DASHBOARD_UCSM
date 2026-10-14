"""Estadísticas de uso del bot a partir de ``logs/bot_comandos.csv`` (sección 9)."""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict

from config import config


class MetricasBotService:
    """Lee el CSV de comandos atendidos y calcula tiempos por tipo de comando."""

    def resumen_por_comando(self) -> list:
        ruta = config.BASE_DIR / "logs" / "bot_comandos.csv"
        if not ruta.exists():
            return []

        tiempos = defaultdict(list)
        conteo_exito = defaultdict(lambda: [0, 0])  # [exitosos, total]

        with open(ruta, newline="", encoding="utf-8") as archivo:
            for fila in csv.DictReader(archivo):
                comando = fila.get("comando", "")
                try:
                    tiempos[comando].append(float(fila["tiempo_respuesta_ms"]))
                except (KeyError, ValueError, TypeError):
                    continue
                conteo_exito[comando][1] += 1
                if fila.get("exito") in ("True", "1", "true"):
                    conteo_exito[comando][0] += 1

        resultado = []
        for comando, valores in sorted(tiempos.items(), key=lambda kv: -len(kv[1])):
            ordenados = sorted(valores)
            n = len(ordenados)
            posicion_p95 = min(int(0.95 * (n - 1)), n - 1) if n else 0
            exitosos, total = conteo_exito[comando]
            resultado.append({
                "comando": comando, "n": n,
                "promedio_ms": round(statistics.fmean(valores), 1) if valores else None,
                "p95_ms": round(ordenados[posicion_p95], 1) if n else None,
                "tasa_exito": round(exitosos / total * 100, 1) if total else None,
            })
        return resultado
