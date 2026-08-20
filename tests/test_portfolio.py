"""Pruebas del motor de riesgo y del simulador de cartera."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from qq_core.catalog import instruments as catalog
from qq_core.domain.signal import Direction, Signal, SignalStrength
from qq_core.portfolio.risk import RiskConfig, select_signals, size_position
from qq_core.portfolio.simulator import simulate_portfolio
from qq_core.strategies.library import build_registry, TrendFollowing

REGISTRY = build_registry()


def _senal(symbol="US500", precio="5000", stop="4900", direccion=Direction.LONG,
           fuerza=SignalStrength.MODERATE) -> Signal:
    """Señal de prueba con el contrato v2 (tres precios separados)."""
    ahora = datetime.now(timezone.utc)
    entrada = Decimal(precio)
    nivel = Decimal(stop) if stop else None
    # El stop debe estar del lado correcto: por debajo en compras, por encima
    # en ventas. El modelo lo valida, así que la prueba debe respetarlo.
    if nivel is not None and direccion is Direction.SHORT and nivel <= entrada:
        nivel = entrada + (entrada - nivel)
    return Signal(
        signal_id=f"t:{symbol}", strategy="test", strategy_version="1.0.0",
        symbol=symbol, as_of=ahora, generated_at=ahora,
        direction=direccion, strength=fuerza,
        observed_price=entrada,
        entry_price=entrada,
        stop_price=nivel if direccion is not Direction.FLAT else None,
        rationale="prueba",
    )


# --------------------------------------------------------------------- #
# Dimensionamiento por riesgo
# --------------------------------------------------------------------- #


def test_riesgo_es_el_porcentaje_configurado() -> None:
    """CA-36: la pérdida al alcanzar el stop es el riesgo objetivo.

    Es la propiedad central del dimensionamiento: independientemente del
    precio o la volatilidad del instrumento, arriesgar el 1% significa perder
    el 1% si se toca la invalidación.
    """
    capital = Decimal("100000")
    # Tope de peso desactivado para verificar la propiedad pura del riesgo.
    cfg = RiskConfig(max_position_weight=Decimal("1.0"))
    unidades, riesgo, _ = size_position(_senal(), capital, cfg)
    assert abs(float(riesgo) - 1000.0) < 1.0
    # 100 puntos de distancia, 1.000 $ de riesgo -> 10 unidades
    assert abs(float(unidades) - 10.0) < 0.01


def test_instrumentos_distintos_mismo_riesgo() -> None:
    """Dos instrumentos con precios muy distintos arriesgan lo mismo.

    Es lo que diferencia dimensionar por riesgo de repartir capital a partes
    iguales: la plata a 30 y un índice a 5.000 reciben tamaños muy distintos
    pero contribuyen igual al riesgo de la cartera.
    """
    capital = Decimal("100000")
    cfg = RiskConfig(max_position_weight=Decimal("1.0"))
    _, riesgo_indice, _ = size_position(
        _senal(precio="5000", stop="4900"), capital, cfg
    )
    _, riesgo_plata, _ = size_position(
        _senal(symbol="XAGUSD", precio="30", stop="28"), capital, cfg
    )
    assert abs(float(riesgo_indice) - float(riesgo_plata)) < 1.0


def test_sin_stop_no_se_opera() -> None:
    """CA-37: una posición sin nivel de invalidación no se puede dimensionar.

    Preferimos no operar a operar con riesgo desconocido.
    """
    unidades, riesgo, motivo = size_position(_senal(stop=None), Decimal("100000"))
    assert unidades == 0
    assert riesgo == 0
    assert "invalidación" in motivo.lower()


def test_senal_flat_no_genera_posicion() -> None:
    unidades, _, _ = size_position(
        _senal(direccion=Direction.FLAT), Decimal("100000")
    )
    assert unidades == 0


def test_tope_de_peso_por_posicion() -> None:
    """Un stop muy cercano no debe producir una posición desproporcionada."""
    cfg = RiskConfig(max_position_weight=Decimal("0.10"))
    capital = Decimal("100000")
    unidades, _, motivo = size_position(
        _senal(precio="5000", stop="4999"), capital, cfg
    )
    assert float(unidades * Decimal("5000")) <= float(capital) * 0.1001
    assert "peso máximo" in motivo


def test_seleccion_prioriza_conviccion() -> None:
    """CA-38: con más candidatas que huecos, mandan las de mayor convicción."""
    senales = [
        _senal(symbol="A", fuerza=SignalStrength.WEAK),
        _senal(symbol="B", fuerza=SignalStrength.STRONG),
        _senal(symbol="C", fuerza=SignalStrength.MODERATE),
    ]
    elegidas = select_signals(senales, RiskConfig(max_positions=2))
    assert [s.symbol for s in elegidas] == ["B", "C"]


def test_seleccion_es_determinista() -> None:
    """Dos ejecuciones con los mismos datos dan la misma cartera."""
    senales = [_senal(symbol=s) for s in ("C", "A", "B")]
    a = [s.symbol for s in select_signals(senales, RiskConfig(max_positions=2))]
    b = [s.symbol for s in select_signals(senales, RiskConfig(max_positions=2))]
    assert a == b == ["A", "B"]


def test_seleccion_descarta_flat() -> None:
    senales = [_senal(symbol="A", direccion=Direction.FLAT), _senal(symbol="B")]
    assert [s.symbol for s in select_signals(senales)] == ["B"]


# --------------------------------------------------------------------- #
# Simulador de cartera
# --------------------------------------------------------------------- #


@pytest.fixture
def mercado() -> tuple[dict, dict]:
    np.random.seed(21)
    precios, instrumentos = {}, {}
    for entry in list(catalog.CATALOG)[:5]:
        n = 900
        fechas = pd.bdate_range("2021-01-04", periods=n)
        px = 5000 * np.exp(np.cumsum(np.random.normal(0.06 / 252, 0.16 / np.sqrt(252), n)))
        precios[entry.symbol] = pd.DataFrame(
            {"open": px * 0.999, "high": px * 1.005, "low": px * 0.995, "close": px},
            index=fechas,
        )
        instrumentos[entry.symbol] = entry.instrument
    return precios, instrumentos


def test_simulacion_produce_curva_de_capital(mercado) -> None:
    precios, instrumentos = mercado
    r = simulate_portfolio(precios, instrumentos, REGISTRY)
    assert len(r.equity_curve) > 0
    assert (r.equity_curve > 0).all()
    assert r.metrics


def test_respeta_el_maximo_de_posiciones(mercado) -> None:
    """CA-39: la cartera nunca excede el límite de posiciones simultáneas."""
    precios, instrumentos = mercado
    cfg = RiskConfig(max_positions=2)
    r = simulate_portfolio(precios, instrumentos, REGISTRY, risk_config=cfg)
    assert r.daily_positions.max() <= 2
    assert len(r.open_positions) <= 2


def test_operaciones_cerradas_tienen_motivo(mercado) -> None:
    """Toda salida registra por qué se produjo: auditoría del Módulo 15."""
    precios, instrumentos = mercado
    r = simulate_portfolio(precios, instrumentos, REGISTRY)
    for t in r.trades:
        assert t.exit_reason in ("Invalidación", "Cambio de señal")
        assert t.exit_date >= t.entry_date


def test_menor_riesgo_produce_menor_volatilidad(mercado) -> None:
    """CA-40: el parámetro de riesgo controla efectivamente la variabilidad.

    Si arriesgar la mitad no redujera la volatilidad, el dimensionamiento no
    estaría funcionando.
    """
    precios, instrumentos = mercado
    alto = simulate_portfolio(
        precios, instrumentos, REGISTRY,
        risk_config=RiskConfig(risk_per_trade_pct=Decimal("2.0")),
    )
    bajo = simulate_portfolio(
        precios, instrumentos, REGISTRY,
        risk_config=RiskConfig(risk_per_trade_pct=Decimal("0.5")),
    )
    assert bajo.metrics["volatility"] < alto.metrics["volatility"]


def test_capital_inicial_se_respeta(mercado) -> None:
    precios, instrumentos = mercado
    r = simulate_portfolio(
        precios, instrumentos, REGISTRY, initial_capital=Decimal("50000")
    )
    assert abs(float(r.equity_curve.iloc[0]) - 50000) < 5000


def test_posiciones_abiertas_tienen_stop(mercado) -> None:
    """Ninguna posición queda abierta sin nivel de invalidación definido."""
    precios, instrumentos = mercado
    r = simulate_portfolio(precios, instrumentos, REGISTRY)
    for p in r.open_positions:
        assert p.stop_price is not None


def test_mercado_vacio_no_falla() -> None:
    r = simulate_portfolio({}, {}, REGISTRY)
    assert r.equity_curve.empty
