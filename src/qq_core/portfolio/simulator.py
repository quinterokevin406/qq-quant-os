"""Simulador de cartera (Módulo 08).

Recorre el histórico día a día tomando las decisiones que habría tomado un
operador siguiendo las señales del sistema: abre, mantiene y cierra posiciones,
aplica los costes reales y lleva la cuenta del capital.

DIFERENCIA CON EL BACKTEST DE UNA ESTRATEGIA
---------------------------------------------
El motor de backtesting (Módulo 06) evalúa una estrategia sobre un instrumento
de forma aislada, con exposición continua. Este simulador es distinto y más
realista:

  - Opera una CARTERA: varias posiciones a la vez, compitiendo por el mismo
    capital, con un límite de posiciones simultáneas.
  - Dimensiona por riesgo, no por exposición teórica.
  - Respeta el capital disponible: si no queda, no abre.
  - Cierra por invalidación (stop) además de por cambio de señal.

Un instrumento puede tener señal de compra y aun así no abrirse, porque los
huecos de la cartera ya están ocupados por señales de mayor convicción. Eso es
lo que ocurre en la práctica y lo que el backtest aislado no captura.

REGLA DE EJECUCIÓN
------------------
Igual que en el Módulo 06: la señal calculada con el cierre del día `t` se
ejecuta a la APERTURA del día `t+1`. Nunca se opera al precio que se usó para
decidir.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from qq_core.backtest.exits import ExitPolicy, apply_exit_policy
from qq_core.domain.instrument import CFD
from qq_core.domain.signal import Direction
from qq_core.features import engine as fx
from qq_core.portfolio.risk import Position, RiskConfig

TRADING_DAYS = 252

_NIVELES_PERMITIDOS: dict[str, tuple[str, ...]] = {
    "strong": ("strong",),
    "moderate": ("strong", "moderate"),
    "weak": ("strong", "moderate", "weak"),
}
"""Niveles de convicción aceptados según el mínimo exigido."""


@dataclass
class Trade:
    """Operación cerrada, para el historial y las estadísticas."""

    symbol: str
    direction: Direction
    units: Decimal
    entry_date: date
    entry_price: Decimal
    exit_date: date
    exit_price: Decimal
    pnl: Decimal
    financing_cost: Decimal
    exit_reason: str
    strategy: str

    @property
    def days_held(self) -> int:
        return (self.exit_date - self.entry_date).days

    @property
    def return_pct(self) -> float:
        base = abs(self.units) * self.entry_price
        return float(self.pnl / base * 100) if base else 0.0

    def to_row(self) -> dict:
        return {
            "Instrumento": self.symbol,
            "Dirección": "Compra" if self.direction is Direction.LONG else "Venta",
            "Entrada": self.entry_date.isoformat(),
            "Salida": self.exit_date.isoformat(),
            "Días": self.days_held,
            "Precio entrada": float(round(self.entry_price, 4)),
            "Precio salida": float(round(self.exit_price, 4)),
            "Resultado $": float(round(self.pnl, 2)),
            "Resultado %": round(self.return_pct, 2),
            "Motivo salida": self.exit_reason,
            "Estrategia": self.strategy,
        }


@dataclass
class PortfolioResult:
    """Resultado completo de una simulación de cartera."""

    equity_curve: pd.Series
    trades: list[Trade] = field(default_factory=list)
    open_positions: list[Position] = field(default_factory=list)
    initial_capital: Decimal = Decimal("100000")
    metrics: dict = field(default_factory=dict)
    daily_positions: pd.Series = field(default_factory=lambda: pd.Series(dtype=int))

    @property
    def final_equity(self) -> Decimal:
        return Decimal(str(round(float(self.equity_curve.iloc[-1]), 2)))

    @property
    def total_return_pct(self) -> float:
        return float(self.final_equity / self.initial_capital - 1) * 100


def simulate_portfolio(
    price_data: dict[str, pd.DataFrame],
    instruments: dict,
    strategies: dict,
    initial_capital: Decimal = Decimal("100000"),
    risk_config: RiskConfig | None = None,
    spread_bps: float = 3.0,
    use_trailing: bool = True,
    min_strength: str | None = None,
) -> PortfolioResult:
    """Simula la gestión de una cartera siguiendo las señales del sistema.

    Args:
        price_data: Precios por símbolo, con columnas open/high/low/close.
        instruments: Instrumentos del catálogo por símbolo, para los costes.
        strategies: Estrategias a ejecutar, por nombre.
        initial_capital: Capital inicial de la cuenta.
        risk_config: Parámetros de gestión de riesgo.
        spread_bps: Coste de transacción en puntos básicos.
        min_strength: Convicción mínima para abrir posición: `strong`,
            `moderate` o `None` para no filtrar. Permite responder a «qué
            habría pasado operando sólo las señales fuertes», que suele
            reducir mucho el número de operaciones y, con costes de
            financiación altos, mejorar el resultado.

    Returns:
        Resultado con curva de capital, operaciones cerradas y posiciones
        abiertas al final del periodo.
    """
    cfg = risk_config or RiskConfig()

    # Calendario común: sólo fechas presentes en todos los instrumentos, para
    # evitar decidir con información parcial de mercados con festivos distintos.
    fechas = None
    for frame in price_data.values():
        idx = frame.index
        fechas = idx if fechas is None else fechas.union(idx)
    if fechas is None or len(fechas) == 0:
        return PortfolioResult(equity_curve=pd.Series(dtype=float))
    fechas = fechas.sort_values()

    # Señales y niveles precalculados por instrumento y estrategia.
    atr_por_simbolo: dict[str, pd.Series] = {
        symbol: fx.atr(frame["high"], frame["low"], frame["close"], 14)
        for symbol, frame in price_data.items()
    }
    señales: dict[tuple[str, str], pd.Series] = {}
    for symbol, frame in price_data.items():
        for nombre, estrategia in strategies.items():
            if len(frame) >= estrategia.warmup_bars + 10:
                posiciones = _posiciones(estrategia, frame, symbol)

                # Filtro por convicción: se anulan las señales que no alcanzan
                # el nivel exigido. Se aplica ANTES de la política de salida
                # para que el trailing stop opere sólo sobre lo que se habría
                # abierto realmente.
                if min_strength:
                    convicciones = estrategia.strength_series(frame)
                    permitidas = _NIVELES_PERMITIDOS[min_strength]
                    posiciones = posiciones.where(
                        convicciones.isin(permitidas), 0.0
                    )
                if use_trailing:
                    # Trailing stop también en la cartera, para que el
                    # histórico refleje las mismas reglas de salida que se
                    # aplicarían operando.
                    posiciones, _ = apply_exit_policy(
                        frame, posiciones, atr_por_simbolo[symbol], ExitPolicy()
                    )
                señales[(symbol, nombre)] = posiciones

    capital = Decimal(str(initial_capital))
    abiertas: dict[str, Position] = {}
    cerradas: list[Trade] = []
    curva: list[float] = []
    n_posiciones: list[int] = []
    costes_acumulados: dict[str, Decimal] = {}

    spread = Decimal(str(spread_bps / 10_000.0))

    for i, fecha in enumerate(fechas):
        dia = fecha.date() if hasattr(fecha, "date") else fecha

        # ---------- 1. Valorar posiciones abiertas a precio de hoy ---------- #
        for symbol, pos in list(abiertas.items()):
            frame = price_data[symbol]
            if fecha not in frame.index:
                continue
            pos.current_price = Decimal(str(round(float(frame.at[fecha, "close"]), 6)))

        # ---------- 2. Coste de financiación de lo que sigue abierto -------- #
        for symbol, pos in abiertas.items():
            inst = instruments.get(symbol)
            fin = getattr(inst, "financing", None) if isinstance(inst, CFD) else None
            if fin is None or pos.current_price is None:
                continue
            tasa = fin.daily_rate(pos.direction is Direction.LONG, dia.weekday())
            coste = pos.notional * tasa
            capital += coste
            costes_acumulados[symbol] = costes_acumulados.get(symbol, Decimal(0)) + coste

        # ---------- 3. Cierres por invalidación (stop) ---------------------- #
        for symbol, pos in list(abiertas.items()):
            frame = price_data[symbol]
            if fecha not in frame.index or pos.stop_price is None:
                continue
            bajo = Decimal(str(round(float(frame.at[fecha, "low"]), 6)))
            alto = Decimal(str(round(float(frame.at[fecha, "high"]), 6)))
            tocado = (
                pos.direction is Direction.LONG and bajo <= pos.stop_price
            ) or (pos.direction is Direction.SHORT and alto >= pos.stop_price)
            if tocado:
                capital += _cerrar(
                    pos, pos.stop_price, dia, "Invalidación", cerradas,
                    costes_acumulados, spread
                )
                del abiertas[symbol]

        # ---------- 4. Cierres por cambio de señal -------------------------- #
        for symbol, pos in list(abiertas.items()):
            clave = (symbol, pos.strategy)
            serie = señales.get(clave)
            if serie is None or fecha not in serie.index:
                continue
            pos_idx = serie.index.get_loc(fecha)
            if pos_idx == 0:
                continue
            deseada = float(serie.iloc[pos_idx - 1])  # decisión de ayer
            actual = 1.0 if pos.direction is Direction.LONG else -1.0
            if deseada != actual:
                frame = price_data[symbol]
                if fecha not in frame.index:
                    continue
                precio = Decimal(str(round(float(frame.at[fecha, "open"]), 6)))
                capital += _cerrar(
                    pos, precio, dia, "Cambio de señal", cerradas,
                    costes_acumulados, spread
                )
                del abiertas[symbol]

        # ---------- 5. Aperturas -------------------------------------------- #
        if len(abiertas) < cfg.max_positions and i > 0:
            candidatas = []
            for (symbol, nombre), serie in señales.items():
                if symbol in abiertas or fecha not in serie.index:
                    continue
                pos_idx = serie.index.get_loc(fecha)
                if pos_idx == 0:
                    continue
                deseada = float(serie.iloc[pos_idx - 1])
                if deseada == 0.0:
                    continue
                frame = price_data[symbol]
                if fecha not in frame.index:
                    continue
                atr = atr_por_simbolo[symbol]
                if fecha not in atr.index or pd.isna(atr.at[fecha]):
                    continue
                candidatas.append((symbol, nombre, deseada, float(atr.at[fecha])))

            candidatas.sort(key=lambda c: c[0])
            equity_actual = capital + sum(
                p.unrealized_pnl for p in abiertas.values()
            )

            for symbol, nombre, deseada, valor_atr in candidatas:
                if len(abiertas) >= cfg.max_positions:
                    break
                frame = price_data[symbol]
                apertura = Decimal(str(round(float(frame.at[fecha, "open"]), 6)))
                if apertura <= 0:
                    continue

                direccion = Direction.LONG if deseada > 0 else Direction.SHORT
                distancia = Decimal(str(round(valor_atr, 6))) * cfg.stop_atr_multiple
                if distancia <= 0:
                    continue
                stop = (
                    apertura - distancia
                    if direccion is Direction.LONG
                    else apertura + distancia
                )

                riesgo = equity_actual * (cfg.risk_per_trade_pct / Decimal(100))
                unidades = riesgo / distancia

                nocional_max = equity_actual * cfg.max_position_weight
                if unidades * apertura > nocional_max:
                    unidades = nocional_max / apertura

                # Restricción de exposición bruta agregada.
                expuesto = sum(p.notional for p in abiertas.values())
                if expuesto + unidades * apertura > equity_actual * cfg.max_gross_exposure:
                    disponible = equity_actual * cfg.max_gross_exposure - expuesto
                    if disponible <= 0:
                        continue
                    unidades = disponible / apertura

                if unidades <= 0:
                    continue

                capital -= unidades * apertura * spread  # coste de entrada
                abiertas[symbol] = Position(
                    symbol=symbol,
                    direction=direccion,
                    units=unidades,
                    entry_price=apertura,
                    entry_date=dia,
                    stop_price=stop,
                    strategy=nombre,
                    current_price=apertura,
                )

        # ---------- 6. Registrar el estado del día -------------------------- #
        equity = capital + sum(p.unrealized_pnl for p in abiertas.values())
        curva.append(float(equity))
        n_posiciones.append(len(abiertas))

    serie_equity = pd.Series(curva, index=fechas, name="capital")
    serie_pos = pd.Series(n_posiciones, index=fechas, name="posiciones")

    return PortfolioResult(
        equity_curve=serie_equity,
        trades=cerradas,
        open_positions=list(abiertas.values()),
        initial_capital=Decimal(str(initial_capital)),
        metrics=_metricas(serie_equity, cerradas),
        daily_positions=serie_pos,
    )


def _posiciones(estrategia, frame: pd.DataFrame, symbol: str) -> pd.Series:
    """Obtiene las posiciones de una estrategia, con o sin símbolo.

    El momento transversal necesita saber sobre qué instrumento opera, porque
    compara unos con otros. Las demás no. Se detecta por firma en lugar de
    obligar a todas a aceptar un parámetro que no usan.
    """
    try:
        return estrategia.target_position(frame, symbol)
    except TypeError:
        return estrategia.target_position(frame)


def _cerrar(
    pos: Position,
    precio: Decimal,
    dia: date,
    motivo: str,
    registro: list[Trade],
    costes: dict[str, Decimal],
    spread: Decimal,
) -> Decimal:
    """Cierra una posición y devuelve el efectivo resultante."""
    movimiento = precio - pos.entry_price
    if pos.direction is Direction.SHORT:
        movimiento = -movimiento
    pnl = movimiento * abs(pos.units)
    coste_salida = abs(pos.units) * precio * spread
    pnl -= coste_salida

    registro.append(
        Trade(
            symbol=pos.symbol,
            direction=pos.direction,
            units=pos.units,
            entry_date=pos.entry_date,
            entry_price=pos.entry_price,
            exit_date=dia,
            exit_price=precio,
            pnl=pnl,
            financing_cost=costes.get(pos.symbol, Decimal(0)),
            exit_reason=motivo,
            strategy=pos.strategy,
        )
    )
    costes[pos.symbol] = Decimal(0)
    return pnl


def _metricas(equity: pd.Series, trades: list[Trade]) -> dict:
    """Métricas de la cartera simulada."""
    if len(equity) < 2:
        return {}

    rets = equity.pct_change().dropna()
    anios = len(equity) / TRADING_DAYS
    total = float(equity.iloc[-1] / equity.iloc[0])
    cagr = total ** (1 / anios) - 1 if anios > 0 and total > 0 else -1.0
    vol = float(rets.std() * np.sqrt(TRADING_DAYS)) if len(rets) else 0.0
    sharpe = cagr / vol if vol > 1e-9 else 0.0
    dd = float(fx.drawdown(equity).min())

    ganadoras = [t for t in trades if t.pnl > 0]
    perdedoras = [t for t in trades if t.pnl <= 0]
    media_gan = float(np.mean([float(t.pnl) for t in ganadoras])) if ganadoras else 0.0
    media_per = float(np.mean([float(t.pnl) for t in perdedoras])) if perdedoras else 0.0

    return {
        "cagr": cagr,
        "total_return": total - 1,
        "volatility": vol,
        "sharpe": sharpe,
        "max_drawdown": dd,
        "trades": len(trades),
        "win_rate": len(ganadoras) / len(trades) if trades else 0.0,
        "avg_win": media_gan,
        "avg_loss": media_per,
        "profit_factor": (
            abs(sum(float(t.pnl) for t in ganadoras) / sum(float(t.pnl) for t in perdedoras))
            if perdedoras and sum(float(t.pnl) for t in perdedoras) != 0
            else 0.0
        ),
        "avg_days_held": (
            float(np.mean([t.days_held for t in trades])) if trades else 0.0
        ),
    }
