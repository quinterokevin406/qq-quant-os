"""Genera señales y ejecuta backtests sobre los datos descargados.

Uso:

    python scripts/generar_senales.py              # señales de hoy + backtest
    python scripts/generar_senales.py --sin-backtest
    python scripts/generar_senales.py --estrategia trend_following

Los resultados se guardan en `senales.csv` y `backtests.csv`, y se muestran
también en el panel visual.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qq_core.backtest.engine import run_backtest  # noqa: E402
from qq_core.catalog import instruments as catalog  # noqa: E402
from qq_core.domain.enums import DataSource, Timeframe  # noqa: E402
from qq_core.domain.instrument import CFD  # noqa: E402
from qq_core.features.engine import bars_to_frame  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402
from qq_core.strategies.library import build_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Genera señales y backtests.")
    parser.add_argument("--db", default="qq_data.db")
    parser.add_argument("--estrategia", default=None,
                        choices=sorted(build_registry()), help="Sólo una estrategia")
    parser.add_argument("--sin-backtest", action="store_true")
    parser.add_argument("--anios", type=int, default=10)
    args = parser.parse_args()

    _REG = build_registry()
    if not Path(args.db).exists():
        print(f"\nNo existe '{args.db}'. Ejecuta primero:")
        print("   python scripts/descargar_datos.py\n")
        return 1

    repo = SQLiteBarRepository(args.db)
    fuente = repo.primary_source()
    if fuente is None:
        print(f"\n'{args.db}' está vacío. Ejecuta primero la descarga.\n")
        return 1

    estrategias = (
        {args.estrategia: _REG[args.estrategia]} if args.estrategia else _REG
    )

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * args.anios)

    senales = []
    resultados = []

    print()
    print("=" * 78)
    print("  QQ QUANT OS — Generación de señales")
    print("=" * 78)

    for nombre, estrategia in estrategias.items():
        print(f"\n  ESTRATEGIA: {nombre}  (v{estrategia.version})")
        print(f"  {'-' * 74}")
        print(f"  {'INSTRUMENTO':<12} {'SEÑAL':<8} {'CONVICCIÓN':<12} "
              f"{'PRECIO':>12}  MOTIVO")

        for entry in catalog.CATALOG:
            bars = repo.get_bars(
                entry.symbol, Timeframe.D1, fuente, start, end
            )
            if len(bars) < estrategia.warmup_bars + 10:
                continue

            precios = bars_to_frame(bars)

            señal = estrategia.explain(precios, entry.symbol)
            if señal is not None:
                senales.append(señal)
                motivo = señal.rationale[:46] + ("..." if len(señal.rationale) > 46 else "")
                print(f"  {entry.symbol:<12} {señal.direction.value:<8} "
                      f"{señal.strength.value:<12} "
                      f"{float(señal.entry_price):>12,.2f}  {motivo}")

            if not args.sin_backtest:
                inst = entry.instrument
                fin = inst.financing if isinstance(inst, CFD) else None
                try:
                    resultados.append(
                        run_backtest(precios, estrategia, entry.symbol, financing=fin)
                    )
                except ValueError:
                    pass

    # ------------------------------------------------------------------ #
    # Exportación
    # ------------------------------------------------------------------ #
    if senales:
        with open("senales.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(senales[0].to_row()))
            writer.writeheader()
            for s in senales:
                writer.writerow(s.to_row())

    if resultados:
        filas = [r.summary_row for r in resultados]
        with open("backtests.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(filas[0]))
            writer.writeheader()
            writer.writerows(filas)

        print()
        print("=" * 78)
        print("  RESULTADOS DE BACKTEST (con costes reales del bróker)")
        print("=" * 78)
        print(f"  {'INSTRUMENTO':<12} {'ESTRATEGIA':<18} {'ANUAL %':>9} "
              f"{'SHARPE':>8} {'CAÍDA MÁX %':>13} {'OPS':>6}")
        print(f"  {'-' * 74}")
        for r in sorted(
            resultados, key=lambda x: x.metrics.get("sharpe", -99), reverse=True
        ):
            m = r.metrics
            print(f"  {r.symbol:<12} {r.strategy:<18} "
                  f"{m.get('cagr', 0) * 100:>8.2f}% {m.get('sharpe', 0):>8.2f} "
                  f"{m.get('max_drawdown', 0) * 100:>12.1f}% "
                  f"{int(m.get('trades', 0)):>6}")

        positivos = [r for r in resultados if r.metrics.get("sharpe", 0) > 0.5]
        print(f"  {'-' * 74}")
        print(f"  Combinaciones evaluadas: {len(resultados)}   "
              f"Con Sharpe > 0.5: {len(positivos)}")
        print()
        print("  AVISO: estos resultados NO están corregidos por selección múltiple.")
        print("  Evaluar muchas combinaciones garantiza encontrar algunas buenas por")
        print("  azar. Antes de operar con dinero real se requiere validación fuera")
        print("  de muestra y corrección estadística (Módulo 05).")

    print()
    print(f"  Señales generadas: {len(senales)}  ->  senales.csv")
    if resultados:
        print(f"  Backtests: {len(resultados)}  ->  backtests.csv")
    print("  Ver en el panel:  streamlit run dashboard/app.py")
    print()

    repo.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
