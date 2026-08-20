"""Motor de backtesting (Módulo 06).

PRINCIPIO: UN BACKTEST DEBE SER PESIMISTA
------------------------------------------
Un backtest optimista es peor que no tener backtest, porque produce confianza
injustificada. Este motor incorpora obligatoriamente:

  1. **Desfase de ejecución.** La señal calculada con el cierre de `t` se
     ejecuta a la apertura de `t+1`. No es configurable.

  2. **Coste de financiación real.** Aplicado a partir de los swaps medidos en
     el bróker (ADR-0003), incluyendo el cargo triple del viernes. Este coste
     es lo que separa un backtest realista de uno de folleto comercial: en el
     universo de CFDs supera el 10% anual en posiciones compradas.

  3. **Coste de transacción.** Diferencial de compraventa aplicado en cada
     cambio de posición.

Lo que este motor NO modela todavía, y debe declararse al presentar resultados:
deslizamiento en momentos de baja liquidez, huecos de apertura, rechazo de
órdenes y el impacto de operar tamaños grandes. Todos empeorarían los
resultados, nunca los mejoran.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import numpy as np
import pandas as pd

from qq_core.backtest.attribution import Attribution, explain_result
from qq_core.backtest.exits import ExitPolicy, apply_exit_policy
from qq_core.domain.instrument import CFD, FinancingTerms
from qq_core.features import engine as fx

TRADING_DAYS = 252


@dataclass(frozen=True)
class BacktestConfig:
    """Supuestos del backtest. Todos explícitos y auditables.

    Attributes:
        initial_capital: Capital inicial en divisa de cuenta.
        target_volatility: Volatilidad anualizada objetivo de la cartera. El
            tamaño de posición se escala para aproximarla.
        max_leverage: Apalancamiento máximo permitido. Limita el escalado por
            volatilidad cuando el activo está muy tranquilo.
        spread_bps: Coste de transacción en puntos básicos sobre el nocional,
            aplicado en cada cambio de posición.
        apply_financing: Si aplicar el coste de mantener posiciones abiertas.
            Ponerlo a `False` sólo sirve para cuantificar cuánto pesa ese
            coste; nunca para presentar resultados.
        vol_window: Ventana de estimación de volatilidad para el sizing.
        exit_policy: Reglas de salida. Por defecto incluye trailing stop, que
            es una mejora estructural: deja correr las ganancias en lugar de
            devolverlas cuando el precio se gira.
        min_strength: Convicción mínima para operar una señal. Filtrar por
            convicción reduce el número de operaciones y suele mejorar el
            resultado, aunque también reduce la exposición.
    """

    initial_capital: float = 100_000.0
    target_volatility: float = 0.15
    max_leverage: float = 1.0
    spread_bps: float = 3.0
    apply_financing: bool = True
    vol_window: int = 20
    exit_policy: ExitPolicy = field(default_factory=ExitPolicy)
    use_exit_policy: bool = True


@dataclass
class BacktestResult:
    """Resultado de un backtest, con sus métricas y series."""

    symbol: str
    strategy: str
    equity: pd.Series
    positions: pd.Series
    returns: pd.Series
    costs: dict[str, float] = field(default_factory=dict)
    metrics: dict[str, float] = field(default_factory=dict)
    attribution: Attribution | None = None

    @property
    def summary_row(self) -> dict:
        """Fila de resumen para tablas comparativas."""
        return {
            "Instrumento": self.symbol,
            "Estrategia": self.strategy,
            "Rentabilidad anual %": round(self.metrics.get("cagr", 0) * 100, 2),
            "Rent. bruta total %": round(self.costs.get("gross_return_pct", 0), 2),
            "Volatilidad %": round(self.metrics.get("volatility", 0) * 100, 2),
            "Sharpe": round(self.metrics.get("sharpe", 0), 2),
            "Caída máxima %": round(self.metrics.get("max_drawdown", 0) * 100, 2),
            "Operaciones": int(self.metrics.get("trades", 0)),
            "Días positivos %": round(self.metrics.get("positive_days", 0) * 100, 1),
            "Tiempo invertido %": round(
                self.metrics.get("time_in_market", 0) * 100, 1
            ),
            "Coste financiación %": round(
                self.costs.get("financing_pct_of_capital", 0), 2
            ),
        }


def compute_metrics(
    equity: pd.Series,
    discrete_positions: pd.Series,
    risk_free_annual: float = 0.0,
) -> dict[str, float]:
    """Métricas de rendimiento y riesgo de una curva de capital.

    Args:
        equity: Serie de capital acumulado.
        discrete_positions: Posiciones DISCRETAS (+1/0/-1), no la exposición
            escalada por volatilidad. Contar operaciones sobre la exposición
            escalada daría una operación por sesión, ya que el escalado cambia
            a diario aunque la posición se mantenga. Es una distinción que
            infla artificialmente el número de operaciones y los costes.

    Returns:
        Diccionario de métricas. Valores neutros si no hay datos suficientes.
    """
    if len(equity) < 2:
        return {}

    rets = equity.pct_change().dropna()
    if rets.empty or equity.iloc[0] <= 0:
        return {}

    anios = len(equity) / TRADING_DAYS
    total = equity.iloc[-1] / equity.iloc[0]
    cagr = total ** (1 / anios) - 1 if anios > 0 and total > 0 else 0.0
    vol = float(rets.std() * np.sqrt(TRADING_DAYS))
    sharpe = float(cagr / vol) if vol > 1e-9 else 0.0

    negativos = rets[rets < 0]
    downside = float(negativos.std() * np.sqrt(TRADING_DAYS)) if len(negativos) else 0.0
    sortino = float(cagr / downside) if downside > 1e-9 else 0.0

    dd = fx.drawdown(equity)
    max_dd = float(dd.min())
    calmar = float(cagr / abs(max_dd)) if abs(max_dd) > 1e-9 else 0.0

    cambios = discrete_positions.diff().fillna(0) != 0
    n_ops = int(cambios.sum())
    dias_positivos = float((rets > 0).mean()) if len(rets) else 0.0
    exposicion = float((discrete_positions != 0).mean())

    # Sharpe clásico: media de rendimientos en exceso sobre su desviación.
    # Coexiste con `sharpe` (cagr/vol) en lugar de sustituirlo, porque las
    # correcciones estadísticas del Módulo 05 están derivadas sobre ESTA
    # definición y no sobre la geométrica. Pasarles cagr/vol produce un número
    # sin error visible, y ese número es incorrecto.
    rf_periodo = risk_free_annual / TRADING_DAYS
    exceso = rets - rf_periodo
    desv_exceso = float(exceso.std(ddof=1))
    sharpe_clasico = (
        float(exceso.mean() / desv_exceso * np.sqrt(TRADING_DAYS))
        if desv_exceso > 1e-12
        else 0.0
    )

    return {
        "cagr": float(cagr),
        "total_return": float(total - 1),
        "volatility": vol,
        "sharpe": sharpe,
        "sharpe_classic": sharpe_clasico,
        "risk_free_annual": float(risk_free_annual),
        "sortino": sortino,
        "max_drawdown": max_dd,
        "calmar": calmar,
        "trades": n_ops,
        "positive_days": dias_positivos,
        "time_in_market": exposicion,
    }


def run_backtest(
    prices: pd.DataFrame,
    strategy,
    symbol: str,
    financing: FinancingTerms | None = None,
    config: BacktestConfig | None = None,
) -> BacktestResult:
    """Ejecuta un backtest de una estrategia sobre un instrumento.

    Args:
        prices: DataFrame con columnas open, high, low, close.
        strategy: Instancia que cumple el protocolo `Strategy`.
        symbol: Símbolo canónico, para el informe.
        financing: Términos de financiación del bróker. Si es `None`, no se
            aplica coste de mantenimiento y así se refleja en el resultado.
        config: Supuestos del backtest.

    Returns:
        Resultado con curva de capital, posiciones, costes y métricas.

    Raises:
        ValueError: Si faltan columnas o no hay datos suficientes.
    """
    cfg = config or BacktestConfig()

    faltan = {"open", "high", "low", "close"} - set(prices.columns)
    if faltan:
        raise ValueError(f"faltan columnas en los precios: {sorted(faltan)}")
    if len(prices) < strategy.warmup_bars + 10:
        raise ValueError(
            f"datos insuficientes: {len(prices)} barras, se necesitan al menos "
            f"{strategy.warmup_bars + 10} para {strategy.name}"
        )

    close = prices["close"]
    señal = strategy.target_position(prices)

    # -------------------------------------------------------------- #
    # DESFASE DE EJECUCIÓN — obligatorio y no configurable.
    # La señal calculada con el cierre de t se aplica a partir de t+1.
    # -------------------------------------------------------------- #
    posicion = señal.shift(1).fillna(0.0)

    # ---------------------------------------------------------------- #
    # Gestión de salidas: trailing stop y objetivo.
    # Se aplica DESPUÉS del desfase de ejecución, sobre la posición que el
    # operador tendría realmente abierta.
    # ---------------------------------------------------------------- #
    motivos_salida = pd.Series([""] * len(close), index=close.index)
    if cfg.use_exit_policy:
        atr_serie = fx.atr(prices["high"], prices["low"], close, 14)
        posicion, motivos_salida = apply_exit_policy(
            prices, posicion, atr_serie, cfg.exit_policy
        )

    # Dimensionamiento por volatilidad: se busca que cada posición aporte una
    # contribución de riesgo similar, en lugar de un nominal similar. Un
    # instrumento con el triple de volatilidad recibe un tercio del tamaño.
    vol = fx.realized_volatility(close, cfg.vol_window)
    escala = (cfg.target_volatility / vol).clip(upper=cfg.max_leverage)
    escala = escala.shift(1).fillna(0.0).replace([np.inf, -np.inf], 0.0)

    exposicion = (posicion * escala).fillna(0.0)

    ret_activo = close.pct_change().fillna(0.0)
    ret_bruto = exposicion * ret_activo

    # ---------------------- Coste de transacción ---------------------- #
    cambio = exposicion.diff().abs().fillna(0.0)
    coste_transaccion = cambio * (cfg.spread_bps / 10_000.0)

    # ---------------------- Coste de financiación --------------------- #
    # La tasa es una fracción del nocional, invariante de escala. No depende
    # del precio del instrumento, lo que elimina la clase de error que
    # producían los swaps expresados en puntos: copiar el valor en puntos de
    # un instrumento a otro daba costes equivocados en varias veces.
    coste_financiacion = pd.Series(0.0, index=close.index)
    if cfg.apply_financing and financing is not None:
        dias = pd.Series(close.index.weekday, index=close.index)
        tasa_larga = float(financing.annual_rate_long) / 365.0
        tasa_corta = float(financing.annual_rate_short) / 365.0

        base = pd.Series(
            np.where(
                exposicion > 0,
                tasa_larga,
                np.where(exposicion < 0, tasa_corta, 0.0),
            ),
            index=close.index,
        )
        multiplicador = pd.Series(
            np.where(dias == financing.triple_swap_weekday, 3.0, 1.0),
            index=close.index,
        )
        coste_financiacion = base * multiplicador * exposicion.abs()

    ret_neto = ret_bruto - coste_transaccion + coste_financiacion

    equity = (1 + ret_neto).cumprod() * cfg.initial_capital
    metricas = compute_metrics(equity, posicion)

    costes = {
        "transaction_total_pct": float(coste_transaccion.sum() * 100),
        "financing_total_pct": float(coste_financiacion.sum() * 100),
        "financing_pct_of_capital": float(coste_financiacion.sum() * 100),
        "gross_return_pct": float(ret_bruto.sum() * 100),
        "net_return_pct": float(ret_neto.sum() * 100),
    }

    atribucion = explain_result(
        returns_gross=ret_bruto,
        financing=coste_financiacion,
        transaction=coste_transaccion,
        exposure=exposicion,
        exit_reasons=motivos_salida,
    )

    return BacktestResult(
        symbol=symbol,
        strategy=strategy.name,
        attribution=atribucion,
        equity=equity,
        positions=exposicion,
        returns=ret_neto,
        costs=costes,
        metrics=metricas,
    )


def financing_from_instrument(instrument) -> FinancingTerms | None:
    """Extrae los términos de financiación de un instrumento del catálogo.

    Cubre CFD y FxSpot. Antes de la v1.10 sólo cubría CFD, de modo que los
    backtests de divisas se ejecutaban SIN coste de financiación alguno. No era
    una aproximación conservadora: era regalar entre 0,8 y 2,7 puntos anuales a
    los pares comprados que en realidad cuestan dinero.

    Devuelve `None` sólo cuando el instrumento realmente no tiene financiación
    declarada. `None` significa "desconocido", y el motor debe tratarlo como
    tal, no como cero.
    """
    financiacion = getattr(instrument, "financing", None)
    if financiacion is not None:
        return financiacion
    if isinstance(instrument, CFD):
        return instrument.financing
    return None
