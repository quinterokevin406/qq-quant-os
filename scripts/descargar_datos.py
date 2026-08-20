"""Descarga de datos históricos de mercado.

Uso desde PowerShell, dentro de la carpeta del proyecto:

    python scripts/descargar_datos.py                  # todo el catálogo
    python scripts/descargar_datos.py --anios 15
    python scripts/descargar_datos.py --simbolos US500 EURUSD
    python scripts/descargar_datos.py --fuente stooq   # proveedor alternativo

Los datos se guardan en `qq_data.db`, un fichero SQLite en la carpeta del
proyecto. No requiere servidor ni instalación adicional.

El script es idempotente: ejecutarlo dos veces no duplica datos. Si el
proveedor ha revisado el histórico, lo detecta y lo reporta.

NOTA SOBRE PROVEEDORES
----------------------
La fuente por defecto es Yahoo Finance. Stooq se conserva como alternativa,
aunque desde agosto de 2026 bloquea las descargas automáticas devolviendo una
página de verificación de navegador.

Ninguno de los dos es de grado institucional. Son adecuados para investigación
exploratoria; para producción deben sustituirse por un proveedor de pago. El
sistema está diseñado para que ese cambio consista en escribir un adaptador.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qq_core.adapters.stooq import StooqProvider  # noqa: E402
from qq_core.adapters.twelvedata import TwelveDataProvider  # noqa: E402
from qq_core.adapters.yahoo import YahooProvider  # noqa: E402
from qq_core.catalog.overlay import active_catalog  # noqa: E402
from qq_core.domain.enums import Timeframe  # noqa: E402
from qq_core.ingestion.service import IngestionRequest, IngestionService  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) QQ-Quant-OS/0.4"
TIMEOUT_SECONDS = 30


def http_get(url: str) -> str:
    """Descarga una URL y devuelve su contenido como texto."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read().decode("utf-8", errors="replace")


def build_provider(nombre: str):
    """Instancia el proveedor solicitado y su extractor de símbolo."""
    if nombre == "yahoo":
        return YahooProvider(), lambda e: e.yahoo_symbol
    if nombre == "stooq":
        return StooqProvider(fetcher=http_get), lambda e: e.stooq_symbol
    if nombre == "twelvedata":
        return TwelveDataProvider(), lambda e: e.twelvedata_symbol
    raise SystemExit(f"Proveedor desconocido: {nombre}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Descarga histórico diario del catálogo QQ Quant OS."
    )
    parser.add_argument("--anios", type=int, default=10)
    parser.add_argument("--simbolos", nargs="*", default=None)
    parser.add_argument("--db", default="qq_data.db")
    parser.add_argument(
        "--fuente", default="yahoo", choices=("yahoo", "stooq", "twelvedata")
    )
    parser.add_argument(
        "--timeframe", default="1d", choices=("5m", "15m", "1h", "4h", "1d"),
        help="Resolución. Las intradía sólo están disponibles en twelvedata.",
    )
    parser.add_argument("--pausa", type=float, default=0.4)
    args = parser.parse_args()

    if args.fuente == "yahoo":
        try:
            import yfinance  # noqa: F401
        except ImportError:
            print()
            print("  Falta la librería 'yfinance'. Instálala con:")
            print()
            print("     pip install yfinance")
            print()
            return 1

    resolucion = Timeframe(args.timeframe)

    if args.fuente == "twelvedata":
        proveedor_prueba = TwelveDataProvider()
        if not proveedor_prueba.is_configured:
            print()
            print("  Falta la clave de Twelve Data.")
            print()
            print("  1. Regístrate gratis en twelvedata.com")
            print("  2. Copia tu clave desde el panel de la cuenta")
            print("  3. En PowerShell, ejecuta:")
            print()
            print('     $env:TWELVEDATA_API_KEY = "tu_clave_aqui"')
            print()
            print("  Para que quede guardada de forma permanente:")
            print()
            print('     [Environment]::SetEnvironmentVariable('
                  '"TWELVEDATA_API_KEY", "tu_clave", "User")')
            print()
            return 1

    if resolucion is not Timeframe.D1 and args.fuente != "twelvedata":
        print()
        print(f"  El timeframe {args.timeframe} sólo está disponible en "
              f"twelvedata.")
        print(f"  Ejecuta:  python scripts/descargar_datos.py "
              f"--fuente twelvedata --timeframe {args.timeframe}")
        print()
        return 1

    provider, get_symbol = build_provider(args.fuente)

    entries = tuple(e for e in active_catalog() if get_symbol(e))
    if args.simbolos:
        pedidos = {s.upper() for s in args.simbolos}
        entries = tuple(e for e in entries if e.symbol in pedidos)
        if not entries:
            print(f"Ningún símbolo del catálogo coincide con {sorted(pedidos)}")
            return 1

    end = datetime.now(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) + timedelta(days=1)
    start = end - timedelta(days=365 * args.anios)

    repo = SQLiteBarRepository(args.db)
    service = IngestionService(provider, repo, max_retries=2, backoff_seconds=1.5)

    print()
    print("=" * 76)
    print("  QQ QUANT OS — Descarga de datos históricos")
    print(f"  Proveedor: {args.fuente}   Resolución: {args.timeframe}   "
          f"Instrumentos: {len(entries)}   Periodo: {args.anios} años")
    print("=" * 76)
    print()
    print(f"  {'INSTRUMENTO':<12} {'TICKER':<12} {'BARRAS':>8}  "
          f"{'DESDE':<12} ESTADO")
    print(f"  {'-' * 72}")

    ok = 0
    total_nuevas = revisiones = 0
    sin_datos: list[str] = []

    for entry in entries:
        ticker = get_symbol(entry)
        req = IngestionRequest(
            provider_symbol=ticker,
            canonical_symbol=entry.symbol,
            timeframe=resolucion,
            start=start,
            end=end,
            resume=False,
        )
        try:
            result = service.ingest(req)
        except Exception as exc:  # noqa: BLE001
            print(f"  {entry.symbol:<12} {ticker:<12} {'-':>8}  {'-':<12} "
                  f"ERROR: {type(exc).__name__}")
            sin_datos.append(f"{entry.symbol} ({ticker})")
            continue

        almacenadas = repo.get_bars(
            entry.symbol, resolucion, provider.capabilities.source, start, end
        )
        if not almacenadas:
            print(f"  {entry.symbol:<12} {ticker:<12} {0:>8}  {'-':<12} SIN DATOS")
            sin_datos.append(f"{entry.symbol} ({ticker})")
        else:
            desde = almacenadas[0].ts.date().isoformat()
            nota = "OK"
            if result.report.revised:
                nota = f"OK ({result.report.revised} revisadas)"
            print(f"  {entry.symbol:<12} {ticker:<12} {len(almacenadas):>8}  "
                  f"{desde:<12} {nota}")
            ok += 1
            total_nuevas += result.report.inserted
            revisiones += result.report.revised

        time.sleep(args.pausa)

    print(f"  {'-' * 72}")
    print()
    print(f"  Instrumentos con datos : {ok} de {len(entries)}")
    print(f"  Barras nuevas          : {total_nuevas:,}")
    print(f"  Barras totales en BD   : {repo.total_bars():,}")
    if revisiones:
        print(f"  Revisiones detectadas  : {revisiones}  "
              f"(el proveedor modificó histórico previo)")
    if sin_datos:
        print()
        print("  Sin respuesta — revisar el identificador en el catálogo:")
        for s in sin_datos:
            print(f"    - {s}")

    print()
    if ok:
        print("  Siguiente paso:  python scripts/generar_senales.py")
    else:
        print("  Ningún instrumento devolvió datos. Prueba el otro proveedor:")
        print("     python scripts/descargar_datos.py --fuente stooq")
    print()

    repo.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
