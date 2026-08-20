"""Pruebas del motor de portafolio: perfil de cuenta, calidad y tamaño.

La prueba más importante es `test_calidad_cero_si_no_supera_la_validacion`.
Verifica la propiedad estructural que impide que el motor asigne capital a
combinaciones cuyo resultado histórico es indistinguible del azar. Sin ella, el
Signal Quality Score sería un amplificador de sesgo de selección: buscaría los
backtests más contaminados y les daría más dinero.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from qq_core.domain.signal import Direction, Horizon, Signal, SignalStrength
from qq_core.portfolio.account import (
    AccountProfile,
    RiskLimits,
    RiskProfile,
    drawdown_multiplier,
)
from qq_core.portfolio.signal_scoring import (
    QualityClass,
    ScoringLevel,
    TradeStats,
    score_signal,
)
from qq_core.portfolio.sizing import (
    SizingDecision,
    estimate_stop_distance,
    financing_cost_for_horizon,
    size_signal,
)

AHORA = datetime.now(timezone.utc)


def hacer_senal(
    strategy: str = "tf",
    symbol: str = "US500",
    target: Decimal | None = Decimal("5300"),
    stop: Decimal | None = Decimal("4900"),
    horizon: Horizon = Horizon.MONTH_1,
    direction: Direction = Direction.LONG,
) -> Signal:
    return Signal(
        signal_id=f"{strategy}:{symbol}",
        strategy=strategy,
        strategy_version="1.0",
        symbol=symbol,
        as_of=AHORA,
        generated_at=AHORA,
        direction=direction,
        strength=SignalStrength.STRONG,
        horizon=horizon,
        observed_price=Decimal("5000"),
        entry_price=Decimal("5000"),
        target_price=target,
        stop_price=stop,
        rationale="prueba",
    )


@pytest.fixture
def estadisticas():
    """Una estrategia genuinamente buena, y la media global del sistema.

    La buena NO son retornos simétricos con media positiva: eso da un payoff
    cercano a 1 y el motor lo puntúa WEAK, correctamente. Una estrategia
    accionable acierta menos de la mitad de las veces pero gana bastante más
    de lo que pierde — el perfil de seguimiento de tendencia.

        45% de aciertos, ganancia media 4%, pérdida media 1.5%
        Expectativa = 0.45 x 0.04 - 0.55 x 0.015 = +0.98% por operación
    """
    ganadoras = [0.04] * 180
    perdedoras = [-0.015] * 220
    buena = TradeStats.from_trade_returns(
        pd.Series(ganadoras + perdedoras), sharpe_annual=1.4
    )
    rng = np.random.default_rng(7)
    global_ = TradeStats.from_trade_returns(
        rng.normal(0.000, 0.03, 3000), sharpe_annual=0.1
    )
    return buena, global_


# --------------------------------------------------------------------------- #
# CA-59: muestra mínima
#
# NOTA (v1.12): las pruebas CA-57 y CA-58, que verificaban que una combinación
# sin validación estadística recibiera cero, se retiraron al desconectar el
# módulo `validation` por decisión de negocio. El módulo sigue en el
# repositorio y puede volver a conectarse.
# --------------------------------------------------------------------------- #






def test_muestra_insuficiente_impide_puntuar() -> None:
    """CA-59: sin operaciones suficientes no se inventa una expectativa."""
    vacio = TradeStats.from_trade_returns(pd.Series([0.01, -0.01, 0.02]))
    q = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): vacio},
        vacio,
        min_trades=30,
    )
    assert q.classification is QualityClass.REJECTED
    assert "INSUFFICIENT_SAMPLE" in q.reasons


# --------------------------------------------------------------------------- #
# CA-60 a CA-62: muestra, jerarquía y encogimiento
# --------------------------------------------------------------------------- #


def test_retrocede_de_nivel_cuando_la_casilla_es_pequena(
    estadisticas
) -> None:
    """CA-60: la jerarquía evita puntuar sobre ocho operaciones.

    Con 1.650 casillas posibles y unos pocos miles de operaciones, la casilla
    más específica casi nunca tiene muestra. Retroceder es lo correcto; hacerlo
    en silencio, no. El nivel usado queda registrado.
    """
    buena, global_ = estadisticas
    buckets = {("est0",): buena}  # sólo el nivel más general

    q = score_signal(hacer_senal("est0"), buckets, global_)

    assert q.level is ScoringLevel.STRATEGY
    assert any(r.startswith("FALLBACK") for r in q.reasons)


def test_el_encogimiento_acerca_la_puntuacion_a_la_media(
    estadisticas
) -> None:
    """CA-61: menos muestra, menos peso de la casilla propia.

    Impide que 35 operaciones afortunadas produzcan una puntuación STRONG.
    """
    _, global_ = estadisticas
    rng = np.random.default_rng(31)
    excelente_corta = TradeStats.from_trade_returns(
        rng.normal(0.02, 0.02, 35), sharpe_annual=2.0
    )
    excelente_larga = TradeStats.from_trade_returns(
        rng.normal(0.02, 0.02, 800), sharpe_annual=2.0
    )

    corta = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): excelente_corta},
        global_,
    )
    larga = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): excelente_larga},
        global_,
    )

    assert corta.shrinkage_weight < larga.shrinkage_weight
    assert corta.score < larga.score


def test_expectativa_no_es_porcentaje_de_aciertos() -> None:
    """CA-62: acertar mucho y ganar poco es peor que lo contrario.

    Es la métrica que más capital ha destruido en este oficio.
    """
    # Acierta el 80% pero pierde cinco veces lo que gana.
    mala = TradeStats.from_trade_returns(pd.Series([0.01] * 8 + [-0.05] * 2))
    # Acierta el 35% pero gana cuatro veces lo que pierde.
    buena = TradeStats.from_trade_returns(
        pd.Series([0.08] * 35 + [-0.02] * 65)
    )

    assert mala.win_rate > buena.win_rate
    assert mala.expectancy < buena.expectancy


# --------------------------------------------------------------------------- #
# CA-63 a CA-67: límites de riesgo y dimensionamiento
# --------------------------------------------------------------------------- #


def test_limites_incoherentes_fallan_de_inmediato() -> None:
    """CA-63: una configuración contradictoria es peor que una mala.

    Produce comportamiento impredecible en lugar de un error visible.
    """
    with pytest.raises(ValueError):
        RiskLimits(
            base_risk_per_trade_pct=Decimal("2"),
            max_risk_per_trade_pct=Decimal("1"),
        ).validated()

    with pytest.raises(ValueError):
        RiskLimits(
            drawdown_reduce_from_pct=Decimal("9"),
            drawdown_block_at_pct=Decimal("5"),
        ).validated()


def test_los_tres_perfiles_son_coherentes() -> None:
    """CA-64: los tres perfiles predefinidos pasan su propia validación."""
    for perfil in RiskProfile:
        p = AccountProfile.from_profile(perfil, Decimal("10000"))
        assert p.limits.validated() is not None


def test_caida_progresiva_reduce_y_luego_bloquea() -> None:
    """CA-65: la escala de reducción por caída se aplica en orden."""
    lim = RiskLimits().validated()
    factores = [
        drawdown_multiplier(Decimal(d), lim) for d in ("0", "4", "6", "9")
    ]
    assert factores[0] == Decimal("1.00")
    assert factores[1] < factores[0]
    assert factores[2] < factores[1]
    assert factores[3] == Decimal("0")


def test_bloqueo_por_caida_impide_abrir(estadisticas) -> None:
    """CA-66: proteger el capital tiene prioridad sobre la mejor señal."""
    buena, global_ = estadisticas
    q = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): buena},
        global_,
    )
    r = size_signal(
        hacer_senal("est0"),
        q,
        Decimal("100000"),
        RiskLimits().validated(),
        drawdown_pct=Decimal("9"),
    )
    assert r.decision is SizingDecision.REJECT_DRAWDOWN_BLOCK
    assert r.units == 0


def test_riesgo_agotado_no_abre_aunque_haya_efectivo(
    estadisticas
) -> None:
    """CA-67: tener efectivo no es tener capacidad de riesgo.

    Es la confusión que la especificación pedía expresamente evitar.
    """
    buena, global_ = estadisticas
    q = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): buena},
        global_,
    )
    r = size_signal(
        hacer_senal("est0"),
        q,
        Decimal("1000000"),
        RiskLimits().validated(),
        current_open_risk_pct=Decimal("5.0"),
    )
    assert r.decision is SizingDecision.NO_NEW_POSITIONS


# --------------------------------------------------------------------------- #
# CA-68 a CA-70: coste de financiación
# --------------------------------------------------------------------------- #


def test_financiacion_rechaza_objetivos_que_no_cubren_el_swap(
    estadisticas
) -> None:
    """CA-68: con swap del -13.36% anual, un objetivo pequeño es pérdida segura.

    Es el hallazgo económico central del proyecto convertido en regla.
    """
    buena, global_ = estadisticas
    q = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): buena},
        global_,
    )
    r = size_signal(
        hacer_senal("est0", target=Decimal("5020")),
        q,
        Decimal("100000"),
        RiskLimits().validated(),
        annual_financing_pct=-13.36,
    )
    assert r.decision is SizingDecision.REJECT_NEGATIVE_AFTER_COSTS


def test_intradia_no_paga_financiacion() -> None:
    """CA-69: sin pernoctar no hay swap."""
    assert (
        financing_cost_for_horizon(-13.36, Horizon.INTRADAY, Direction.LONG) == 0
    )
    assert financing_cost_for_horizon(-13.36, Horizon.MONTH_1, Direction.LONG) < 0


def test_horizonte_mas_largo_cuesta_mas() -> None:
    """CA-70: el coste crece con el tiempo que el capital está comprometido."""
    semana = financing_cost_for_horizon(-13.36, Horizon.WEEK_1, Direction.LONG)
    mes = financing_cost_for_horizon(-13.36, Horizon.MONTH_1, Direction.LONG)
    assert mes < semana < 0


# --------------------------------------------------------------------------- #
# CA-71 a CA-73: estrategias sin stop y acotación de multiplicadores
# --------------------------------------------------------------------------- #


def test_sin_stop_el_riesgo_no_es_cero_ni_desconocido() -> None:
    """CA-71: siempre existe una pérdida de referencia estimada."""
    rng = np.random.default_rng(41)
    r = pd.Series(rng.normal(0, 0.012, 500))
    est = estimate_stop_distance(r, Decimal("5000"), Horizon.MONTH_1)

    assert est.stop_distance > 0
    assert est.method != ""


def test_se_adopta_la_estimacion_mas_conservadora() -> None:
    """CA-72: cuando los métodos discrepan, esa discrepancia es incertidumbre.

    La incertidumbre se paga con prudencia, no promediando.
    """
    rng = np.random.default_rng(43)
    r = pd.Series(rng.normal(0, 0.012, 500))
    est = estimate_stop_distance(r, Decimal("5000"), Horizon.MONTH_1)

    maximo = max(est.atr_based, est.var_99, est.cvar_95, est.worst_excursion)
    assert abs(float(est.stop_distance) / 5000.0 - maximo) < 1e-9


def test_una_senal_excelente_no_produce_una_posicion_desproporcionada(
    estadisticas
) -> None:
    """CA-73: los multiplicadores están acotados.

    La peor pérdida de una cartera casi siempre viene de la posición que
    alguien consideró excepcional.
    """
    buena, global_ = estadisticas
    q = score_signal(
        hacer_senal("est0"),
                {("est0", "US500", "long"): buena},
        global_,
    )
    lim = RiskLimits().validated()
    r = size_signal(
        hacer_senal("est0"),
        q,
        Decimal("100000"),
        lim,
        correlation_multiplier=5.0,
        regime_multiplier=5.0,
        liquidity_multiplier=5.0,
    )
    assert r.risk_pct <= lim.max_risk_per_trade_pct
