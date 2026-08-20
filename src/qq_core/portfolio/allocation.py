"""Asignación de capital: cuánto poner en cada señal (Módulo 08).

LA PREGUNTA QUE RESPONDE
------------------------
El sistema dice «comprar US500» y «vender USOIL». Con 100.000 dólares,
¿cuánto va a cada una? Repartir a partes iguales ignora que unas señales son
más fiables que otras y que unos instrumentos se mueven mucho más que otros.

Este módulo asigna un porcentaje del capital a cada señal combinando cuatro
factores:

  1. **Calidad histórica de la estrategia** — cómo se ha comportado.
  2. **Convicción de la señal** — fuerte, moderada o débil.
  3. **Volatilidad del instrumento** — el gas natural necesita menos peso que
     un índice para aportar el mismo riesgo.
  4. **Correlación con lo ya asignado** — cinco índices europeos no son cinco
     apuestas, son casi la misma repetida.

ADVERTENCIA METODOLÓGICA: EL PASADO PREDICE MAL
-----------------------------------------------
Asignar más capital a la estrategia que mejor lo hizo históricamente es
«perseguir rentabilidad», y está documentado que funciona mal: las estrategias
alternan periodos buenos y malos, y suele asignarse más justo antes de que se
giren.

Por eso este módulo aplica **contracción hacia el peso igual**: el ajuste por
calidad existe pero es moderado, y nunca lleva a concentrar todo en una
estrategia. El parámetro `shrinkage` controla cuánto se confía en el
histórico: 0 es reparto igual, 1 sería confianza plena. El valor por defecto,
0.5, refleja una postura deliberadamente escéptica.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import numpy as np
import pandas as pd

from qq_core.domain.signal import Direction, Signal, SignalStrength

# Multiplicador de riesgo según la convicción declarada por la estrategia.
# Deliberadamente comprimido: una señal fuerte recibe el doble que una débil,
# no diez veces más. La convicción es un juicio cualitativo, no una
# probabilidad medida.
PESO_CONVICCION: dict[SignalStrength, float] = {
    SignalStrength.STRONG: 1.5,
    SignalStrength.MODERATE: 1.0,
    SignalStrength.WEAK: 0.5,
}

MIN_OPERACIONES_FIABLES = 30
"""Operaciones mínimas para tomarse en serio el histórico de una estrategia.

Por debajo de este número, el resultado observado es indistinguible del azar y
la calidad se contrae fuertemente hacia el valor neutro.
"""


@dataclass(frozen=True)
class StrategyScore:
    """Calidad histórica de una estrategia, con su grado de fiabilidad.

    Attributes:
        strategy: Nombre de la estrategia.
        sharpe: Ratio de Sharpe observado.
        hit_rate: Porcentaje de operaciones ganadoras.
        trades: Número de operaciones. Determina cuánta confianza merece.
        quality: Puntuación final entre 0 y 2, donde 1 es neutro.
        reliable: Si hay operaciones suficientes para tomarse en serio la cifra.
    """

    strategy: str
    sharpe: float
    hit_rate: float
    trades: int
    quality: float
    reliable: bool

    @property
    def explanation(self) -> str:
        if not self.reliable:
            return (
                f"Sólo {self.trades} operaciones: histórico insuficiente para "
                f"diferenciarla. Recibe peso neutro."
            )
        if self.quality > 1.2:
            return (
                f"Comportamiento histórico por encima de la media "
                f"(Sharpe {self.sharpe:.2f}). Recibe algo más de peso."
            )
        if self.quality < 0.8:
            return (
                f"Comportamiento histórico por debajo de la media "
                f"(Sharpe {self.sharpe:.2f}). Recibe menos peso."
            )
        return f"Comportamiento en línea con la media (Sharpe {self.sharpe:.2f})."


@dataclass
class Allocation:
    """Capital asignado a una señal concreta."""

    symbol: str
    strategy: str
    strategy_label: str
    direction: Direction
    strength: SignalStrength
    horizon: str

    risk_pct: float
    """Porcentaje del capital que se arriesga si se toca la invalidación."""

    capital_pct: float
    """Porcentaje del capital comprometido como valor nocional."""

    capital_amount: float
    units: float
    entry_price: float
    stop_price: float | None
    target_price: float | None

    strategy_score: float
    correlation_penalty: float
    reasons: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        return {
            "Instrumento": self.symbol,
            "Señal": (
                "COMPRA" if self.direction is Direction.LONG else "VENTA"
            ),
            "Convicción": self.strength.value,
            "Horizonte": self.horizon,
            "% del capital": round(self.capital_pct, 2),
            "Importe $": round(self.capital_amount, 2),
            "Unidades": round(self.units, 4),
            "Riesgo %": round(self.risk_pct, 2),
            "Precio entrada": self.entry_price,
            "Invalidación": self.stop_price,
            "Objetivo": self.target_price,
            "Estrategia": self.strategy_label,
            "Motivo del peso": " · ".join(self.reasons),
        }


def score_strategies(
    backtest_rows: pd.DataFrame, shrinkage: float = 0.5
) -> dict[str, StrategyScore]:
    """Puntúa cada estrategia por su comportamiento histórico.

    Args:
        backtest_rows: Resultados de backtest con columnas `Estrategia`,
            `Sharpe` y `Operaciones`.
        shrinkage: Cuánta confianza dar al histórico, entre 0 y 1. Con 0 todas
            las estrategias reciben el mismo peso; con 1 se confía plenamente
            en lo observado. El valor por defecto es deliberadamente escéptico.

    Returns:
        Puntuación por estrategia, con 1.0 como valor neutro.
    """
    if backtest_rows.empty or "Estrategia" not in backtest_rows:
        return {}

    agregado = backtest_rows.groupby("Estrategia").agg(
        sharpe=("Sharpe", "mean"),
        operaciones=("Operaciones", "sum"),
    )
    if "Días positivos %" in backtest_rows:
        agregado["aciertos"] = backtest_rows.groupby("Estrategia")[
            "Días positivos %"
        ].mean()
    else:
        agregado["aciertos"] = 50.0

    sharpes = agregado["sharpe"].to_numpy(dtype=float)
    media = float(np.nanmean(sharpes)) if len(sharpes) else 0.0
    desviacion = float(np.nanstd(sharpes)) if len(sharpes) > 1 else 0.0

    puntuaciones: dict[str, StrategyScore] = {}
    for nombre, fila in agregado.iterrows():
        sharpe = float(fila["sharpe"])
        operaciones = int(fila["operaciones"])

        # Posición relativa frente al resto de estrategias, acotada para que
        # una sola no acapare el presupuesto.
        if desviacion > 1e-9:
            z = np.clip((sharpe - media) / desviacion, -1.5, 1.5)
        else:
            z = 0.0
        bruta = 1.0 + z * 0.4

        # Contracción por número de observaciones: con pocas operaciones, el
        # resultado observado no distingue habilidad de suerte.
        fiable = operaciones >= MIN_OPERACIONES_FIABLES
        confianza = min(1.0, operaciones / MIN_OPERACIONES_FIABLES)
        efectiva = shrinkage * confianza

        calidad = 1.0 + (bruta - 1.0) * efectiva

        puntuaciones[str(nombre)] = StrategyScore(
            strategy=str(nombre),
            sharpe=sharpe,
            hit_rate=float(fila["aciertos"]),
            trades=operaciones,
            quality=float(np.clip(calidad, 0.4, 1.8)),
            reliable=fiable,
        )
    return puntuaciones


def correlation_penalty(
    symbol: str,
    already_assigned: list[str],
    returns: dict[str, pd.Series],
    threshold: float = 0.7,
) -> tuple[float, str | None]:
    """Reduce el peso de un instrumento muy correlacionado con los ya elegidos.

    Cinco índices europeos no son cinco apuestas distintas: son casi la misma
    repetida cinco veces. Sin esta penalización, la cartera parecería
    diversificada y concentraría todo el riesgo en un único factor.

    Args:
        symbol: Instrumento candidato.
        already_assigned: Instrumentos ya incluidos en la cartera.
        returns: Rentabilidades diarias por instrumento.
        threshold: Correlación a partir de la cual se penaliza.

    Returns:
        Tupla `(factor, explicación)`. El factor multiplica el peso; 1.0
        significa sin penalización.
    """
    if not already_assigned or symbol not in returns:
        return 1.0, None

    serie = returns[symbol]
    maxima = 0.0
    pareja = ""
    for otro in already_assigned:
        if otro == symbol or otro not in returns:
            continue
        comun = serie.index.intersection(returns[otro].index)
        if len(comun) < 60:
            continue
        corr = float(serie.loc[comun].corr(returns[otro].loc[comun]))
        if not np.isnan(corr) and abs(corr) > maxima:
            maxima, pareja = abs(corr), otro

    if maxima <= threshold:
        return 1.0, None

    # Penalización proporcional al exceso sobre el umbral: una correlación de
    # 0.95 reduce mucho más que una de 0.75.
    exceso = (maxima - threshold) / (1.0 - threshold)
    factor = max(0.3, 1.0 - exceso * 0.7)
    return factor, f"correlación {maxima:.0%} con {pareja}"


def allocate_capital(
    signals: list[Signal],
    capital: Decimal,
    strategy_scores: dict[str, StrategyScore] | None = None,
    returns: dict[str, pd.Series] | None = None,
    base_risk_pct: float = 1.0,
    max_positions: int = 8,
    max_capital_pct: float = 25.0,
    max_total_risk_pct: float = 6.0,
    min_strength: SignalStrength | None = None,
) -> list[Allocation]:
    """Reparte el capital entre las señales disponibles.

    Args:
        signals: Señales candidatas.
        capital: Capital total de la cuenta.
        strategy_scores: Calidad histórica por estrategia.
        returns: Rentabilidades diarias, para medir correlaciones.
        base_risk_pct: Riesgo base por operación, antes de ajustes.
        max_positions: Número máximo de posiciones.
        max_capital_pct: Tope de capital por posición.
        max_total_risk_pct: Riesgo agregado máximo de toda la cartera. Es la
            protección más importante: impide que ocho posiciones del 1,5%
            sumen un 12% de riesgo simultáneo.
        min_strength: Convicción mínima exigida.

    Returns:
        Asignaciones ordenadas de mayor a menor peso.
    """
    puntuaciones = strategy_scores or {}
    series = returns or {}
    orden_conviccion = {
        SignalStrength.STRONG: 0, SignalStrength.MODERATE: 1, SignalStrength.WEAK: 2
    }

    candidatas = [
        s for s in signals
        if s.direction is not Direction.FLAT and s.stop_price is not None
    ]
    if min_strength is not None:
        minimo = orden_conviccion[min_strength]
        candidatas = [
            s for s in candidatas if orden_conviccion[s.strength] <= minimo
        ]

    candidatas.sort(
        key=lambda s: (
            orden_conviccion[s.strength],
            -(s.risk_reward or 0),
            s.symbol,
        )
    )

    asignaciones: list[Allocation] = []
    elegidos: list[str] = []
    riesgo_acumulado = 0.0
    capital_float = float(capital)

    for señal in candidatas:
        if len(asignaciones) >= max_positions:
            break
        if riesgo_acumulado >= max_total_risk_pct:
            break

        motivos: list[str] = []

        # --- 1. Convicción de la señal --- #
        factor_conviccion = PESO_CONVICCION[señal.strength]
        motivos.append(f"convicción {señal.strength.value}")

        # --- 2. Calidad histórica de la estrategia --- #
        etiqueta = señal.strategy_label or señal.strategy
        puntuacion = puntuaciones.get(etiqueta) or puntuaciones.get(señal.strategy)
        factor_calidad = puntuacion.quality if puntuacion else 1.0
        if puntuacion and puntuacion.reliable:
            if factor_calidad > 1.15:
                motivos.append("estrategia sólida históricamente")
            elif factor_calidad < 0.85:
                motivos.append("estrategia floja históricamente")
        elif puntuacion:
            motivos.append("histórico corto, peso neutro")

        # --- 3. Correlación con lo ya asignado --- #
        factor_corr, nota_corr = correlation_penalty(señal.symbol, elegidos, series)
        if nota_corr:
            motivos.append(f"reducido por {nota_corr}")

        # --- 4. Riesgo resultante --- #
        riesgo_pct = base_risk_pct * factor_conviccion * factor_calidad * factor_corr

        # Respetar el presupuesto agregado de riesgo.
        disponible = max_total_risk_pct - riesgo_acumulado
        if riesgo_pct > disponible:
            riesgo_pct = disponible
            motivos.append("limitado por el riesgo total de la cartera")
        if riesgo_pct <= 0.05:
            continue

        # --- 5. Traducir riesgo a unidades y capital --- #
        entrada = float(señal.entry_price)
        stop = float(señal.stop_price)
        distancia = abs(entrada - stop)
        if distancia <= 0 or entrada <= 0:
            continue

        importe_riesgo = capital_float * riesgo_pct / 100
        unidades = importe_riesgo / distancia
        nocional = unidades * entrada
        capital_pct = nocional / capital_float * 100

        if capital_pct > max_capital_pct:
            # Un stop muy cercano produciría una posición enorme aunque el
            # riesgo calculado sea correcto. El tope de capital lo impide.
            factor = max_capital_pct / capital_pct
            unidades *= factor
            nocional *= factor
            capital_pct = max_capital_pct
            riesgo_pct *= factor
            motivos.append(f"limitado al {max_capital_pct:.0f}% del capital")

        asignaciones.append(
            Allocation(
                symbol=señal.symbol,
                strategy=señal.strategy,
                strategy_label=etiqueta,
                direction=señal.direction,
                strength=señal.strength,
                horizon=señal.horizon.label,
                risk_pct=riesgo_pct,
                capital_pct=capital_pct,
                capital_amount=nocional,
                units=unidades,
                entry_price=entrada,
                stop_price=stop,
                target_price=(
                    float(señal.target_price) if señal.target_price else None
                ),
                strategy_score=factor_calidad,
                correlation_penalty=factor_corr,
                reasons=motivos,
            )
        )
        elegidos.append(señal.symbol)
        riesgo_acumulado += riesgo_pct

    asignaciones.sort(key=lambda a: a.capital_pct, reverse=True)
    return asignaciones


def allocation_summary(allocations: list[Allocation], capital: Decimal) -> dict:
    """Resumen agregado del reparto, para el panel."""
    if not allocations:
        return {
            "posiciones": 0, "riesgo_total_pct": 0.0, "capital_usado_pct": 0.0,
            "capital_libre_pct": 100.0, "importe_total": 0.0,
        }
    riesgo = sum(a.risk_pct for a in allocations)
    usado = sum(a.capital_pct for a in allocations)
    return {
        "posiciones": len(allocations),
        "riesgo_total_pct": riesgo,
        "capital_usado_pct": usado,
        "capital_libre_pct": max(0.0, 100.0 - usado),
        "importe_total": sum(a.capital_amount for a in allocations),
        "perdida_maxima": float(capital) * riesgo / 100,
    }
