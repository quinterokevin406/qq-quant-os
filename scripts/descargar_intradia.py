"""Descarga de datos intradía de los 23 instrumentos desde Twelve Data.

POR QUÉ UN SCRIPT APARTE
-------------------------
`descargar_datos.py` ya sabe descargar intradía. Lo que no sabe es respetar el
límite de peticiones del plan gratuito de Twelve Data: 8 por minuto y unas 800
al día. Con 23 instrumentos y 2 resoluciones son 46 peticiones, y sin control
de ritmo la mitad fallan.

Este script añade ese control y descarga las dos resoluciones de una vez.

LO QUE ESTE SCRIPT NO PUEDE DAR, Y HAY QUE SABERLO
----------------------------------------------------
El plan gratuito de Twelve Data guarda SEMANAS de histórico intradía, no años.

Eso significa que estos datos sirven para VER el mercado en 5 minutos y 1 hora,
y para contrastar los precios diarios de Yahoo con una segunda fuente. NO
sirven para backtestear estrategias intradía: con unas semanas de muestra, la
diferencia entre una estrategia buena y la suerte es indistinguible.

Producir un backtest intradía con estos datos daría un número con aspecto de
resultado y sin ningún contenido. Para eso hace falta el gateway MT5: el
terminal del bróker guarda años de velas de 1 minuto y 1 hora, y además son los
precios reales a los que se ejecutaría.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

from qq_core.adapters.twelvedata import TwelveDataProvider  # noqa: E402
from qq_core.catalog.overlay import active_catalog  # noqa: E402
from qq_core.domain.enums import Timeframe  # noqa: E402
from qq_core.ingestion.service import IngestionRequest, IngestionService  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402

PETICIONES_POR_MINUTO = 8
"""Límite del plan gratuito. Superarlo devuelve error y pierde la descarga."""

MARGEN = 1.15
"""Margen de seguridad sobre el ritmo teórico, por si el reloj del servidor
difiere del nuestro. Prefiere tardar algo más a que falle la mitad."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="qq_data.db")
    parser.add_argument("--dias", type=int, default=30,
                        help="Días de histórico a pedir. El plan gratuito "
                             "limita el resultado real a unas semanas.")
    parser.add_argument("--timeframes", nargs="*", default=["1h", "5m"])
    parser.add_argument("--simbolos", nargs="*", default=None)
    args = parser.parse_args()

    provider = TwelveDataProvider()
    if not provider.is_configured:
        print()
        print("  Falta la clave de Twelve Data.")
        print()
        print("  1. Regístrate gratis en twelvedata.com")
        print("  2. Copia la clave desde el panel")
        print("  3. En PowerShell:")
        print()
        print('     [Environment]::SetEnvironmentVariable('
              '"TWELVEDATA_API_KEY", "tu_clave", "User")')
        print()
        print("  Cierra y abre PowerShell para que tome efecto.")
        print()
        return 1

    resoluciones = []
    for t in args.timeframes:
        try:
            resoluciones.append(Timeframe(t))
        except ValueError:
            print(f"  Resolución desconocida: {t}")
            return 1

    entries = tuple(e for e in active_catalog() if e.twelvedata_symbol)
    if args.simbolos:
        pedidos = {s.upper() for s in args.simbolos}
        entries = tuple(e for e in entries if e.symbol in pedidos)

    if not entries:
        print("  Ningún instrumento del catálogo tiene símbolo de Twelve Data.")
        return 1

    total_peticiones = len(entries) * len(resoluciones)
    pausa = 60.0 / PETICIONES_POR_MINUTO * MARGEN
    minutos = total_peticiones * pausa / 60.0

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=args.dias)

    repo = SQLiteBarRepository(args.db)
    service = IngestionService(provider, repo, max_retries=2, backoff_seconds=5.0)

    print()
    print("=" * 76)
    print("  QQ QUANT OS — Descarga intradía (Twelve Data)")
    print(f"  Instrumentos: {len(entries)}   Resoluciones: "
          f"{', '.join(t.value for t in resoluciones)}")
    print(f"  Peticiones: {total_peticiones}   Tiempo estimado: "
          f"{minutos:.1f} minutos")
    print("=" * 76)
    print()
    print("  El ritmo está limitado a 8 peticiones por minuto porque es el")
    print("  máximo del plan gratuito. La espera es intencionada.")
    print()
    print(f"  {'INSTRUMENTO':<12} {'RESOL':<6} {'BARRAS':>8}  ESTADO")
    print(f"  {'-' * 60}")

    ok = fallos = 0
    hecho = 0

    for tf in resoluciones:
        for entry in entries:
            hecho += 1
            req = IngestionRequest(
                provider_symbol=entry.twelvedata_symbol,
                canonical_symbol=entry.symbol,
                timeframe=tf,
                start=start,
                end=end,
                resume=False,
            )
            try:
                resultado = service.ingest(req)
                n = getattr(resultado, "stored", None) or getattr(
                    resultado, "bars_stored", 0
                )
                print(f"  {entry.symbol:<12} {tf.value:<6} {n:>8}  ok")
                ok += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  {entry.symbol:<12} {tf.value:<6} {'-':>8}  "
                      f"ERROR: {type(exc).__name__}: {str(exc)[:40]}")
                fallos += 1

            if hecho < total_peticiones:
                time.sleep(pausa)

    print(f"  {'-' * 60}")
    print(f"  Correctas: {ok}   Fallidas: {fallos}")
    print()
    print("  RECORDATORIO: el plan gratuito guarda semanas de intradía, no")
    print("  años. Estos datos sirven para ver el mercado y para contrastar")
    print("  Yahoo, NO para backtestear estrategias intradía.")
    print()

    repo.close()
    return 0 if fallos == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
