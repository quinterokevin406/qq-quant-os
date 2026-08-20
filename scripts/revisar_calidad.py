"""Informe de calidad de datos desde la línea de comandos.

Uso:

    python scripts/revisar_calidad.py
    python scripts/revisar_calidad.py --detalle
    python scripts/revisar_calidad.py --anios 5

Analiza todas las series almacenadas y señala cuáles no son fiables. Devuelve
código de salida 1 si alguna serie resulta inutilizable, para poder usarlo en
la actualización automática y detener el proceso si los datos llegan mal.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qq_core.catalog.overlay import active_catalog  # noqa: E402
from qq_core.domain.enums import Timeframe  # noqa: E402
from qq_core.features.engine import bars_to_frame  # noqa: E402
from qq_core.quality.checks import check_universe, usable_symbols  # noqa: E402
from qq_core.quality.issues import Severity  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Revisa la calidad de las series almacenadas."
    )
    parser.add_argument("--db", default="qq_data.db")
    parser.add_argument("--anios", type=int, default=10)
    parser.add_argument("--detalle", action="store_true",
                        help="Muestra cada problema encontrado")
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"\nNo existe '{args.db}'. Ejecuta primero la descarga.\n")
        return 1

    repo = SQLiteBarRepository(args.db)
    fuente = repo.primary_source()
    if fuente is None:
        print("\nLa base de datos está vacía.\n")
        return 1

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * args.anios)

    frames = {}
    for entry in active_catalog():
        bars = repo.get_bars(entry.symbol, Timeframe.D1, fuente, start, end)
        if bars:
            frames[entry.symbol] = bars_to_frame(bars)

    if not frames:
        print("\nNo hay series para analizar.\n")
        return 1

    informes = check_universe(frames)
    aptas = usable_symbols(informes)

    print()
    print("=" * 78)
    print("  QQ QUANT OS — Control de calidad de datos")
    print("=" * 78)
    print()
    print(f"  {'INSTRUMENTO':<12} {'PUNTOS':>7}  {'ESTADO':<15} "
          f"{'APTA':<5} PROBLEMAS")
    print(f"  {'-' * 74}")

    for symbol in sorted(informes, key=lambda s: informes[s].score):
        informe = informes[symbol]
        tipos = sorted({i.issue_type.value for i in informe.issues})
        resumen = ", ".join(tipos)[:34] or "ninguno"
        print(f"  {symbol:<12} {informe.score:>7.1f}  "
              f"{informe.status_label:<15} "
              f"{'sí' if informe.is_usable else 'NO':<5} {resumen}")

    print(f"  {'-' * 74}")
    print()

    criticas = [r for r in informes.values() if r.critical_issues]
    media = sum(r.score for r in informes.values()) / len(informes)

    print(f"  Series analizadas    : {len(informes)}")
    print(f"  Aptas para señales   : {len(aptas)}")
    print(f"  Puntuación media     : {media:.1f} sobre 100")
    print(f"  Con fallos críticos  : {len(criticas)}")

    if args.detalle:
        print()
        print("  DETALLE DE LOS PROBLEMAS")
        print(f"  {'-' * 74}")
        for symbol in sorted(informes):
            problemas = informes[symbol].issues
            if not problemas:
                continue
            print(f"\n  {symbol}")
            for p in problemas:
                marca = {
                    Severity.CRITICAL: "[CRÍTICO]",
                    Severity.WARNING: "[aviso]  ",
                    Severity.INFO: "[info]   ",
                }[p.severity]
                print(f"    {marca} {p.issue_type.value}")
                for linea in _envolver(p.detail, 68):
                    print(f"              {linea}")

    if criticas:
        print()
        print("  ATENCIÓN: las siguientes series NO se usarán para generar")
        print("  señales hasta que se corrijan sus datos:")
        for informe in criticas:
            print(f"    - {informe.symbol}")

    print()
    repo.close()
    return 1 if criticas else 0


def _envolver(texto: str, ancho: int) -> list[str]:
    palabras, lineas, actual = texto.split(), [], ""
    for palabra in palabras:
        if len(actual) + len(palabra) + 1 > ancho:
            lineas.append(actual)
            actual = palabra
        else:
            actual = f"{actual} {palabra}".strip()
    if actual:
        lineas.append(actual)
    return lineas


if __name__ == "__main__":
    raise SystemExit(main())
