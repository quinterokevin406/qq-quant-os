"""Actualización diaria automática.

Diseñado para ejecutarse solo cada mañana a las 7:00 de Nueva York mediante el
Programador de tareas de Windows. Ver `ACTUALIZACION AUTOMATICA.md`.

Qué hace, en orden:
    1. Descarga los datos de mercado del día.
    2. Genera las señales de las nueve estrategias.
    3. Guarda `senales_del_dia.csv` y `informe_qq_quant_os.html`.
    4. Registra todo en `logs/actualizacion.log`.

Uso manual:
    python scripts/actualizar_diario.py
    python scripts/actualizar_diario.py --sin-informe

POR QUÉ 7:00 DE NUEVA YORK
---------------------------
Las bolsas estadounidenses abren a las 9:30 hora local. A las 7:00 los datos
del cierre anterior ya están consolidados en el proveedor, y quedan dos horas
y media para que el operador revise las señales antes de la apertura.
"""

from __future__ import annotations

import argparse
import csv
import logging
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from qq_core.adapters.yahoo import YahooProvider  # noqa: E402
from qq_core.catalog.user_catalog import effective_catalog  # noqa: E402
from qq_core.domain.enums import Timeframe  # noqa: E402
from qq_core.ingestion.service import IngestionRequest, IngestionService  # noqa: E402
from qq_core.signals.service import generate_signals, load_universe  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402

NY = ZoneInfo("America/New_York")
LOG_DIR = RAIZ / "logs"


def configurar_registro() -> logging.Logger:
    """Registro en fichero y en consola.

    El fichero es lo que permite diagnosticar un fallo ocurrido a las 7 de la
    mañana sin que nadie estuviera delante.
    """
    LOG_DIR.mkdir(exist_ok=True)
    logger = logging.getLogger("actualizacion")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formato = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s")

    fichero = logging.FileHandler(LOG_DIR / "actualizacion.log", encoding="utf-8")
    fichero.setFormatter(formato)
    logger.addHandler(fichero)

    consola = logging.StreamHandler()
    consola.setFormatter(formato)
    logger.addHandler(consola)
    return logger


def main() -> int:
    parser = argparse.ArgumentParser(description="Actualización diaria del sistema.")
    parser.add_argument("--anios", type=int, default=10)
    parser.add_argument("--db", default=str(RAIZ / "qq_data.db"))
    parser.add_argument("--sin-informe", action="store_true")
    args = parser.parse_args()

    log = configurar_registro()
    inicio = datetime.now(timezone.utc)

    log.info("=" * 62)
    log.info("ACTUALIZACIÓN DIARIA — %s hora de Nueva York",
             datetime.now(NY).strftime("%d/%m/%Y %H:%M"))
    log.info("=" * 62)

    # ---------------- 1. Descarga ---------------- #
    repo = SQLiteBarRepository(args.db)
    provider = YahooProvider()
    service = IngestionService(provider, repo, max_retries=3, backoff_seconds=2.0)

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * args.anios)
    entradas = [e for e in effective_catalog() if e.yahoo_symbol]

    log.info("Descargando %s instrumentos...", len(entradas))
    ok = nuevas = revisadas = 0
    fallidos: list[str] = []

    for entry in entradas:
        try:
            resultado = service.ingest(IngestionRequest(
                provider_symbol=entry.yahoo_symbol,
                canonical_symbol=entry.symbol,
                timeframe=Timeframe.D1,
                start=start, end=end, resume=False,
            ))
            nuevas += resultado.report.inserted
            revisadas += resultado.report.revised
            ok += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("  %s falló: %s", entry.symbol, type(exc).__name__)
            fallidos.append(entry.symbol)

    log.info("Descarga: %s de %s correctos, %s barras nuevas",
             ok, len(entradas), nuevas)
    if revisadas:
        log.warning("El proveedor revisó %s barras del histórico. Los backtests "
                    "previos sobre esas series ya no reproducen exactamente.",
                    revisadas)
    if fallidos:
        log.warning("Sin datos: %s", ", ".join(fallidos))

    # ---------------- 2. Señales ---------------- #
    fuente = repo.primary_source()
    if fuente is None:
        log.error("La base de datos está vacía. Se aborta.")
        return 1

    log.info("Generando señales...")
    precios = load_universe(repo, effective_catalog(), fuente, args.anios)
    reporte = generate_signals(precios, effective_catalog())

    accionables = reporte.actionable
    log.info("Señales: %s en total, %s accionables, datos hasta %s",
             len(reporte.signals), len(accionables), reporte.data_as_of)

    for nombre, cuenta in reporte.counts_by_strategy().items():
        if cuenta["compras"] or cuenta["ventas"]:
            log.info("  %-28s %s compras, %s ventas",
                     cuenta["label"], cuenta["compras"], cuenta["ventas"])

    if reporte.errors:
        for e in reporte.errors[:10]:
            log.warning("  %s", e)

    # ---------------- 3. Exportación ---------------- #
    if accionables:
        destino = RAIZ / "senales_del_dia.csv"
        with open(destino, "w", newline="", encoding="utf-8") as fh:
            escritor = csv.DictWriter(fh, fieldnames=list(accionables[0].to_row()))
            escritor.writeheader()
            for s in accionables:
                escritor.writerow(s.to_row())
        log.info("Señales guardadas en %s", destino.name)

    if not args.sin_informe:
        log.info("Generando informe...")
        try:
            subprocess.run(
                [sys.executable, str(RAIZ / "scripts" / "generar_informe.py")],
                cwd=str(RAIZ), check=True, capture_output=True, timeout=600,
            )
            log.info("Informe generado: informe_qq_quant_os.html")
        except Exception as exc:  # noqa: BLE001
            log.warning("El informe falló: %s", exc)

    duracion = (datetime.now(timezone.utc) - inicio).total_seconds()
    log.info("Completado en %.0f segundos", duracion)
    log.info("")

    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
