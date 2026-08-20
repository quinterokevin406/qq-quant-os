"""Órdenes del día: qué ejecutar mañana en el bróker.

Uso:

    python scripts/ordenes_del_dia.py
    python scripts/ordenes_del_dia.py --capital 250000 --riesgo 0.5

Es la salida final del sistema para el operador. Todo lo demás de la
plataforma —adquisición de datos, indicadores, estrategias, backtesting—
existe para producir esta lista.

Cada orden indica el instrumento, la dirección, cuántas unidades, el precio de
referencia, el nivel de invalidación y cuánto capital se arriesga. El operador
decide si la ejecuta.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from qq_core.catalog.overlay import active_catalog  # noqa: E402
from qq_core.domain.enums import Timeframe  # noqa: E402
from qq_core.domain.signal import Direction  # noqa: E402
from qq_core.features.engine import bars_to_frame  # noqa: E402
from qq_core.portfolio.risk import Order, RiskConfig, select_signals, size_position  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402
from qq_core.strategies.library import build_registry  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Órdenes del día para el operador.")
    parser.add_argument("--db", default="qq_data.db")
    parser.add_argument("--capital", type=float, default=100_000)
    parser.add_argument("--riesgo", type=float, default=1.0,
                        help="Porcentaje del capital arriesgado por operación")
    parser.add_argument("--max-posiciones", type=int, default=8)
    parser.add_argument("--anios", type=int, default=5)
    args = parser.parse_args()

    if not Path(args.db).exists():
        print(f"\nNo existe '{args.db}'. Ejecuta primero la descarga de datos.\n")
        return 1

    repo = SQLiteBarRepository(args.db)
    fuente = repo.primary_source()
    if fuente is None:
        print("\nLa base de datos está vacía.\n")
        return 1

    cfg = RiskConfig(
        risk_per_trade_pct=Decimal(str(args.riesgo)),
        max_positions=args.max_posiciones,
    )
    capital = Decimal(str(args.capital))

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * args.anios)

    todas = []
    for entry in active_catalog():
        bars = repo.get_bars(entry.symbol, Timeframe.D1, fuente, start, end)
        if not bars:
            continue
        frame = bars_to_frame(bars)
        for estrategia in build_registry().values():
            if len(frame) < estrategia.warmup_bars + 10:
                continue
            s = estrategia.explain(frame, entry.symbol, entry.instrument.name)
            if s and s.direction is not Direction.FLAT:
                todas.append(s)

    seleccionadas = select_signals(todas, cfg)

    ordenes: list[Order] = []
    nocional_acumulado = Decimal(0)
    limite_nocional = capital * cfg.max_gross_exposure
    descartadas_por_exposicion = 0

    for s in seleccionadas:
        unidades, riesgo, explicacion = size_position(s, capital, cfg)
        if unidades <= 0:
            continue

        # Restricción de exposición agregada. El bróker permite
        # apalancamiento 100:1, pero eso es una facilidad de margen, no una
        # guía de dimensionamiento: la suma de posiciones no debe superar el
        # capital disponible salvo decisión explícita.
        nocional = unidades * s.entry_price
        if nocional_acumulado + nocional > limite_nocional:
            disponible = limite_nocional - nocional_acumulado
            if disponible <= 0:
                descartadas_por_exposicion += 1
                continue
            factor = disponible / nocional
            unidades *= factor
            riesgo *= factor
            nocional = disponible
        nocional_acumulado += nocional

        ordenes.append(
            Order(
                action="ABRIR",
                symbol=s.symbol,
                direction=s.direction,
                units=unidades,
                reference_price=s.entry_price,
                stop_price=s.stop_price,
                risk_amount=riesgo,
                notional=unidades * s.entry_price,
                strategy=s.strategy,
                rationale=s.rationale,
            )
        )

    fecha_dato = max((s.as_of.date() for s in todas), default=None)

    print()
    print("=" * 84)
    print("  QQ QUANT OS — ÓRDENES PARA EJECUCIÓN MANUAL")
    print(f"  Capital: ${float(capital):,.0f}   "
          f"Riesgo por operación: {args.riesgo}%   "
          f"Datos hasta: {fecha_dato}")
    print("=" * 84)
    print()

    if not ordenes:
        print("  No hay órdenes recomendadas para hoy.")
        print("  El sistema recomienda permanecer fuera del mercado.")
        print()
        repo.close()
        return 0

    print(f"  {'#':<3} {'INSTRUMENTO':<11} {'ACCIÓN':<8} {'UNIDADES':>11} "
          f"{'PRECIO':>11} {'INVALIDACIÓN':>13} {'RIESGO $':>10}")
    print(f"  {'-' * 80}")

    riesgo_total = Decimal(0)
    nocional_total = Decimal(0)
    for i, o in enumerate(ordenes, 1):
        accion = "COMPRAR" if o.direction is Direction.LONG else "VENDER"
        stop = f"{float(o.stop_price):,.2f}" if o.stop_price else "—"
        print(f"  {i:<3} {o.symbol:<11} {accion:<8} {float(o.units):>11.3f} "
              f"{float(o.reference_price):>11,.2f} {stop:>13} "
              f"{float(o.risk_amount):>10,.0f}")
        riesgo_total += o.risk_amount
        nocional_total += o.notional

    print(f"  {'-' * 80}")
    print()
    print(f"  Órdenes             : {len(ordenes)}")
    print(f"  Riesgo total        : ${float(riesgo_total):,.0f}  "
          f"({float(riesgo_total / capital * 100):.2f}% del capital)")
    print(f"  Exposición nocional : ${float(nocional_total):,.0f}  "
          f"({float(nocional_total / capital * 100):.0f}% del capital, "
          f"límite {float(cfg.max_gross_exposure * 100):.0f}%)")
    if descartadas_por_exposicion:
        print(f"  Descartadas         : {descartadas_por_exposicion} señales sin "
              f"exposición disponible")
    print()
    print("  JUSTIFICACIÓN DE CADA ORDEN")
    print(f"  {'-' * 80}")
    for i, o in enumerate(ordenes, 1):
        print(f"  {i}. {o.symbol} — {o.strategy}")
        for linea in _envolver(o.rationale, 76):
            print(f"     {linea}")
        print()

    with open("ordenes.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(ordenes[0].to_row()))
        writer.writeheader()
        for o in ordenes:
            writer.writerow(o.to_row())

    print(f"  Guardado en: ordenes.csv")
    print()
    print("  El sistema NO ejecuta estas órdenes. Son una recomendación que el")
    print("  operador debe revisar y decidir si ejecuta manualmente en MT5.")
    print()

    repo.close()
    return 0


def _envolver(texto: str, ancho: int) -> list[str]:
    palabras = texto.split()
    lineas, actual = [], ""
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
