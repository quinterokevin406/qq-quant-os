"""Conversión de presupuesto de riesgo a tamaño de posición.

PRINCIPIO
---------
Las estrategias dicen QUÉ y en qué dirección. Nunca CUÁNTO. El tamaño lo decide
este módulo a partir del presupuesto de riesgo, y sólo de él.

Es una separación deliberada: una estrategia que pudiera decidir su propio
tamaño acabaría, por evolución natural del ajuste, pidiendo más capital cuando
su backtest se ve mejor, que es exactamente el circuito que produce ruina.

LA CADENA DE MULTIPLICADORES
-----------------------------
    RiesgoOperación = Equity x RiesgoBase x Fcalidad x Frégimen
                      x Fcorrelación x Fcaída x Fliquidez

Todos los factores están ACOTADOS. Sin cota, una señal excepcional en un
momento excepcional produciría una posición desproporcionada, y la peor pérdida
de una cartera casi siempre viene de la posición que alguien consideró
excepcional.

ESTRATEGIAS SIN STOP FIJO
--------------------------
Si una estrategia no define un nivel de invalidación, el riesgo NO es cero ni
desconocido. Se estima con una batería de métodos y se toma la MÁS
CONSERVADORA de las estimaciones, no la media: cuando los métodos discrepan,
esa discrepancia es incertidumbre, y la incertidumbre se paga con prudencia.

    - Múltiplo de ATR ajustado por el horizonte previsto
    - VaR histórico al 99%
    - Expected Shortfall (CVaR) al 95%
    - Peor excursión adversa observada

COSTE DE FINANCIACIÓN
----------------------
Este bróker cobra hasta un 13.36% anual por mantener posiciones compradas. Una
posición larga a tres meses arrastra en torno a un 3.3% de coste antes de que
el mercado se mueva. Ese coste se descuenta del beneficio esperado ANTES de
decidir si la operación merece capital. Ignorarlo sobrestima sistemáticamente
la rentabilidad de todo lo comprado.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from qq_core.domain.signal import Direction, Horizon, Signal
from qq_core.portfolio.account import RiskLimits, drawdown_multiplier
from qq_core.portfolio.signal_scoring import QualityClass, QualityScore

DIAS_POR_HORIZONTE: dict[Horizon, int] = {
    Horizon.INTRADAY: 1,
    Horizon.DAY_1: 1,
    Horizon.WEEK_1: 5,
    Horizon.WEEK_2: 10,
    Horizon.MONTH_1: 22,
}
"""Sesiones previstas por horizonte. Determina el coste de financiación."""


class SizingDecision(StrEnum):
    """Decisión del motor sobre una señal."""

    ACCEPT_FULL_SIZE = "ACCEPT_FULL_SIZE"
    ACCEPT_REDUCED_SIZE = "ACCEPT_REDUCED_SIZE"
    WAIT_FOR_CAPACITY = "WAIT_FOR_CAPACITY"
    REJECT_LOW_QUALITY = "REJECT_LOW_QUALITY"
    REJECT_CORRELATION = "REJECT_CORRELATION"
    REJECT_RISK_LIMIT = "REJECT_RISK_LIMIT"
    REJECT_DRAWDOWN_BLOCK = "REJECT_DRAWDOWN_BLOCK"
    REJECT_NEGATIVE_AFTER_COSTS = "REJECT_NEGATIVE_AFTER_COSTS"
    NO_NEW_POSITIONS = "NO_NEW_POSITIONS"

    @property
    def is_accept(self) -> bool:
        return self in (
            SizingDecision.ACCEPT_FULL_SIZE,
            SizingDecision.ACCEPT_REDUCED_SIZE,
        )


@dataclass(frozen=True)
class RiskEstimate:
    """Riesgo estimado de una operación sin stop declarado.

    Attributes:
        stop_distance: Distancia de invalidación adoptada, en precio.
        method: Método que produjo la estimación adoptada.
        atr_based: Estimación por múltiplo de ATR.
        var_99: Estimación por VaR histórico al 99%.
        cvar_95: Estimación por Expected Shortfall al 95%.
        worst_excursion: Peor excursión adversa observada.
    """

    stop_distance: Decimal
    method: str
    atr_based: float
    var_99: float
    cvar_95: float
    worst_excursion: float


def estimate_stop_distance(
    returns: pd.Series,
    price: Decimal,
    horizon: Horizon,
    atr: float | None = None,
    atr_multiple: float = 2.0,
) -> RiskEstimate:
    """Estima una distancia de invalidación cuando la estrategia no la define.

    Args:
        returns: Rendimientos diarios históricos del instrumento.
        price: Precio de referencia.
        horizon: Horizonte previsto. Escala la volatilidad por raíz del tiempo.
        atr: Rango medio verdadero, si se conoce.
        atr_multiple: Múltiplo de ATR a usar.

    Returns:
        Estimación con el detalle de cada método.
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    dias = DIAS_POR_HORIZONTE.get(horizon, 10)
    escala = float(np.sqrt(dias))
    p = float(price)

    if r.size < 30:
        # Sin muestra, una distancia por defecto prudente del 5%.
        d = Decimal(str(p * 0.05))
        return RiskEstimate(d, "por_defecto_sin_muestra", 0.05, 0.05, 0.05, 0.05)

    est_atr = (atr * atr_multiple * escala / p) if atr else float(r.std() * 2.0 * escala)
    est_var = float(abs(np.percentile(r, 1))) * escala
    cola = r[r <= np.percentile(r, 5)]
    est_cvar = float(abs(cola.mean())) * escala if cola.size else est_var
    est_peor = float(abs(r.min())) * escala

    # La MÁS CONSERVADORA, no la media. Discrepancia entre métodos es
    # incertidumbre, y la incertidumbre se paga con prudencia.
    adoptada = max(est_atr, est_var, est_cvar, est_peor)
    metodo = {
        est_atr: "atr",
        est_var: "var_99",
        est_cvar: "cvar_95",
        est_peor: "peor_excursion",
    }[adoptada]

    return RiskEstimate(
        stop_distance=Decimal(str(p * adoptada)),
        method=metodo,
        atr_based=est_atr,
        var_99=est_var,
        cvar_95=est_cvar,
        worst_excursion=est_peor,
    )


@dataclass(frozen=True)
class SizedPosition:
    """Recomendación de tamaño para una señal.

    Attributes:
        signal_id: Señal de origen.
        symbol: Instrumento.
        direction: Dirección.
        decision: Decisión del motor.
        units: Unidades recomendadas.
        entry_price: Precio de entrada de referencia.
        stop_price: Nivel de invalidación.
        stop_distance: Distancia al stop.
        risk_amount: Pérdida si se alcanza el stop.
        risk_pct: Ese riesgo sobre el equity, en porcentaje.
        notional: Exposición nocional.
        financing_cost_pct: Coste de financiación previsto del horizonte.
        expected_profit: Beneficio si se alcanza el objetivo, neto de
            financiación.
        risk_reward: Relación beneficio/riesgo neta.
        multipliers: Cada factor aplicado, para trazabilidad.
        reasons: Códigos de explicación.
        explanation: Explicación en lenguaje llano.
    """

    signal_id: str
    symbol: str
    direction: Direction
    decision: SizingDecision
    units: Decimal
    entry_price: Decimal
    stop_price: Decimal
    stop_distance: Decimal
    risk_amount: Decimal
    risk_pct: Decimal
    notional: Decimal
    financing_cost_pct: Decimal
    expected_profit: Decimal | None
    risk_reward: Decimal | None
    multipliers: dict[str, float]
    reasons: tuple[str, ...]
    explanation: str

    def to_row(self) -> dict[str, Any]:
        return {
            "Instrumento": self.symbol,
            "Dirección": self.direction.value,
            "Decisión": self.decision.value,
            "Unidades": float(self.units),
            "Entrada": float(self.entry_price),
            "Invalidación": float(self.stop_price),
            "Riesgo": float(self.risk_amount),
            "Riesgo %": float(self.risk_pct),
            "Nocional": float(self.notional),
            "Coste financiación %": float(self.financing_cost_pct),
            "Beneficio/Riesgo": (
                float(self.risk_reward) if self.risk_reward is not None else None
            ),
            "Motivos": ", ".join(self.reasons),
        }


def quality_multiplier(score: QualityScore) -> float:
    """Factor por calidad de la señal. Acotado entre 0.5 y 1.3.

    El techo de 1.3 es deliberadamente bajo. Una señal excelente merece algo
    más de capital, no el doble: la diferencia entre una puntuación de 85 y una
    de 70 está dentro del error de estimación de la propia puntuación.
    """
    if score.classification is QualityClass.STRONG:
        return min(1.0 + (score.score - 80.0) / 100.0, 1.3)
    if score.classification is QualityClass.MODERATE:
        return 0.8
    return 0.5


def financing_cost_for_horizon(
    annual_financing_pct: float, horizon: Horizon, direction: Direction
) -> Decimal:
    """Coste de financiación previsto del horizonte, en porcentaje del nocional.

    Args:
        annual_financing_pct: Coste anual en porcentaje. Negativo significa que
            se paga; positivo, que se cobra.
        horizon: Horizonte previsto.
        direction: Dirección de la posición.

    Returns:
        Coste del periodo. Negativo es coste, positivo es ingreso.
    """
    if horizon is Horizon.INTRADAY:
        return Decimal("0")
    dias = DIAS_POR_HORIZONTE.get(horizon, 10)
    return Decimal(str(annual_financing_pct * dias / 365.0))


def size_signal(
    signal: Signal,
    quality: QualityScore,
    equity: Decimal,
    limits: RiskLimits,
    current_open_risk_pct: Decimal = Decimal("0"),
    drawdown_pct: Decimal = Decimal("0"),
    correlation_multiplier: float = 1.0,
    regime_multiplier: float = 1.0,
    liquidity_multiplier: float = 1.0,
    annual_financing_pct: float = -9.0,
    estimated_stop: RiskEstimate | None = None,
) -> SizedPosition:
    """Calcula el tamaño recomendado de una señal.

    Args:
        signal: Señal a dimensionar.
        quality: Puntuación de calidad, que ya incorpora la validación.
        equity: Equity actual de la cuenta.
        limits: Límites de riesgo vigentes.
        current_open_risk_pct: Riesgo ya comprometido por posiciones abiertas
            y señales aprobadas pendientes de ejecutar.
        drawdown_pct: Caída actual desde máximos, en positivo.
        correlation_multiplier: Factor por correlación con la cartera actual.
        regime_multiplier: Factor por régimen de mercado.
        liquidity_multiplier: Factor por liquidez del instrumento.
        annual_financing_pct: Coste de financiación anual del instrumento.
        estimated_stop: Estimación de invalidación si la señal no la trae.

    Returns:
        Recomendación de tamaño con su explicación.
    """
    motivos: list[str] = []
    vacio = Decimal("0")

    def rechazo(dec: SizingDecision, motivo: str, texto: str) -> SizedPosition:
        return SizedPosition(
            signal_id=signal.signal_id,
            symbol=signal.symbol,
            direction=signal.direction,
            decision=dec,
            units=vacio,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price or vacio,
            stop_distance=vacio,
            risk_amount=vacio,
            risk_pct=vacio,
            notional=vacio,
            financing_cost_pct=vacio,
            expected_profit=None,
            risk_reward=None,
            multipliers={},
            reasons=(motivo,),
            explanation=texto,
        )

    # --- Barreras duras, en orden de severidad ---
    if not quality.classification.is_actionable:
        return rechazo(
            SizingDecision.REJECT_LOW_QUALITY,
            "REJECT_LOW_QUALITY",
            f"Calidad {quality.score:.0f}/100 ({quality.classification.value}). "
            f"{quality.explanation}",
        )

    f_dd = drawdown_multiplier(drawdown_pct, limits)
    if f_dd == 0:
        return rechazo(
            SizingDecision.REJECT_DRAWDOWN_BLOCK,
            "REJECT_DRAWDOWN_BLOCK",
            f"Caída del {drawdown_pct:.2f}% sobre el límite de bloqueo del "
            f"{limits.drawdown_block_at_pct}%. No se abren posiciones nuevas "
            f"hasta recuperar. Proteger el capital tiene prioridad sobre "
            f"aprovechar la señal.",
        )

    riesgo_disponible = limits.max_total_open_risk_pct - current_open_risk_pct
    if riesgo_disponible <= 0:
        return rechazo(
            SizingDecision.NO_NEW_POSITIONS,
            "REJECT_RISK_LIMIT",
            f"El riesgo abierto ({current_open_risk_pct:.2f}%) agota el "
            f"presupuesto total ({limits.max_total_open_risk_pct}%). Tener "
            f"efectivo disponible no es lo mismo que tener capacidad de riesgo.",
        )

    # --- Distancia de invalidación ---
    if signal.stop_price is not None and signal.stop_price > 0:
        distancia = abs(signal.entry_price - signal.stop_price)
        stop = signal.stop_price
        motivos.append("STOP_FROM_STRATEGY")
    elif estimated_stop is not None:
        distancia = estimated_stop.stop_distance
        stop = (
            signal.entry_price - distancia
            if signal.direction is Direction.LONG
            else signal.entry_price + distancia
        )
        motivos.append(f"STOP_ESTIMATED_{estimated_stop.method.upper()}")
    else:
        return rechazo(
            SizingDecision.REJECT_RISK_LIMIT,
            "NO_STOP_AVAILABLE",
            "La señal no trae nivel de invalidación y no se ha podido "
            "estimar uno. Sin distancia de invalidación no hay forma de "
            "convertir riesgo en tamaño.",
        )

    if distancia <= 0:
        return rechazo(
            SizingDecision.REJECT_RISK_LIMIT,
            "INVALID_STOP",
            "La distancia de invalidación es cero o negativa.",
        )

    # --- Cadena de multiplicadores, todos acotados ---
    f_calidad = quality_multiplier(quality)
    f_corr = min(max(correlation_multiplier, 0.3), 1.0)
    f_reg = min(max(regime_multiplier, 0.5), 1.2)
    f_liq = min(max(liquidity_multiplier, 0.5), 1.0)

    multiplicadores = {
        "calidad": f_calidad,
        "regimen": f_reg,
        "correlacion": f_corr,
        "caida": float(f_dd),
        "liquidez": f_liq,
    }

    riesgo_pct = (
        limits.base_risk_per_trade_pct
        * Decimal(str(f_calidad))
        * Decimal(str(f_reg))
        * Decimal(str(f_corr))
        * f_dd
        * Decimal(str(f_liq))
    )

    # --- Techos: por operación y por capacidad restante ---
    riesgo_pedido = riesgo_pct
    riesgo_pct = min(riesgo_pct, limits.max_risk_per_trade_pct, riesgo_disponible)

    if riesgo_pct < riesgo_pedido:
        motivos.append("CAPPED_BY_LIMIT")

    if f_corr < 1.0:
        motivos.append("CORRELATION_PENALTY")
    if f_dd < 1:
        motivos.append("DRAWDOWN_REDUCTION")

    importe_riesgo = equity * riesgo_pct / Decimal(100)
    unidades = (importe_riesgo / distancia).quantize(
        Decimal("0.01"), rounding=ROUND_DOWN
    )

    if unidades <= 0:
        return rechazo(
            SizingDecision.WAIT_FOR_CAPACITY,
            "SIZE_ROUNDS_TO_ZERO",
            f"El riesgo permitido ({importe_riesgo:.2f}) dividido por la "
            f"distancia de invalidación ({distancia:.2f}) da menos de la "
            f"unidad mínima negociable. Hace falta más capital o un stop más "
            f"cercano.",
        )

    nocional = unidades * signal.entry_price

    # --- Techo por peso nocional ---
    nocional_max = equity * limits.max_gross_exposure
    if nocional > nocional_max:
        unidades = (nocional_max / signal.entry_price).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        nocional = unidades * signal.entry_price
        importe_riesgo = unidades * distancia
        riesgo_pct = importe_riesgo / equity * Decimal(100)
        motivos.append("CAPPED_BY_NOTIONAL")

    # --- Coste de financiación del horizonte ---
    coste_fin = financing_cost_for_horizon(
        annual_financing_pct, signal.horizon, signal.direction
    )

    beneficio = None
    ratio = None
    if signal.target_price is not None and signal.target_price > 0:
        bruto = abs(signal.target_price - signal.entry_price) * unidades
        coste = nocional * abs(coste_fin) / Decimal(100) if coste_fin < 0 else vacio
        beneficio = bruto - coste
        ratio = (
            (beneficio / importe_riesgo).quantize(Decimal("0.01"))
            if importe_riesgo > 0
            else None
        )

        if beneficio <= 0:
            return rechazo(
                SizingDecision.REJECT_NEGATIVE_AFTER_COSTS,
                "REJECT_NEGATIVE_AFTER_COSTS",
                f"El objetivo de la señal no cubre el coste de financiación. "
                f"Beneficio bruto {bruto:.2f} frente a un coste de "
                f"{coste:.2f} por mantener la posición {signal.horizon.value}. "
                f"Operar esto es perder dinero aunque el mercado acierte.",
            )

    decision = (
        SizingDecision.ACCEPT_FULL_SIZE
        if riesgo_pct >= limits.base_risk_per_trade_pct
        else SizingDecision.ACCEPT_REDUCED_SIZE
    )

    explicacion = (
        f"Riesgo base {limits.base_risk_per_trade_pct}% ajustado por calidad "
        f"(x{f_calidad:.2f}), régimen (x{f_reg:.2f}), correlación "
        f"(x{f_corr:.2f}), caída (x{f_dd}) y liquidez (x{f_liq:.2f}) da "
        f"{riesgo_pct:.3f}% = {importe_riesgo:.2f}. Con invalidación a "
        f"{distancia:.2f}, salen {unidades} unidades y un nocional de "
        f"{nocional:.2f}. Coste de financiación previsto del horizonte: "
        f"{coste_fin:.2f}%."
    )

    return SizedPosition(
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        direction=signal.direction,
        decision=decision,
        units=unidades,
        entry_price=signal.entry_price,
        stop_price=stop,
        stop_distance=distancia,
        risk_amount=importe_riesgo,
        risk_pct=riesgo_pct,
        notional=nocional,
        financing_cost_pct=coste_fin,
        expected_profit=beneficio,
        risk_reward=ratio,
        multipliers=multiplicadores,
        reasons=tuple(motivos),
        explanation=explicacion,
    )
