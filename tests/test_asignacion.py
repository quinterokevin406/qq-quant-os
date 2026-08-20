"""Pruebas del reparto de capital entre señales (Módulo 08).

La propiedad más importante que protegen: el riesgo agregado de la cartera
nunca supera el presupuesto. Ocho posiciones «prudentes» del 1,5% suman un
12% de riesgo simultáneo, que no es prudente en absoluto.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from qq_core.domain.signal import Direction, Horizon, Signal, SignalStrength
from qq_core.portfolio.allocation import (
    MIN_OPERACIONES_FIABLES,
    allocate_capital,
    allocation_summary,
    correlation_penalty,
    score_strategies,
)


def _senal(symbol="US500", fuerza=SignalStrength.MODERATE, precio="5000",
           stop="4900", estrategia="trend_following") -> Signal:
    ahora = datetime.now(timezone.utc)
    return Signal(
        signal_id=f"{estrategia}:{symbol}", strategy=estrategia,
        strategy_label=estrategia, strategy_version="1.0.0", symbol=symbol,
        as_of=ahora, generated_at=ahora, direction=Direction.LONG,
        strength=fuerza, horizon=Horizon.WEEK_2,
        observed_price=Decimal(precio), entry_price=Decimal(precio),
        stop_price=Decimal(stop), target_price=Decimal("5300"),
        rationale="prueba",
    )


def test_riesgo_total_respeta_el_presupuesto() -> None:
    """CA-64: LA PROTECCIÓN MÁS IMPORTANTE DEL MÓDULO.

    Sin este tope, ocho posiciones del 1,5% sumarían un 12% de riesgo
    simultáneo. Una racha adversa normal vaciaría la cuenta.
    """
    señales = [
        _senal(symbol=f"SYM{i}", fuerza=SignalStrength.STRONG) for i in range(15)
    ]
    reparto = allocate_capital(
        señales, Decimal("100000"), max_total_risk_pct=6.0, max_positions=15
    )
    assert sum(a.risk_pct for a in reparto) <= 6.01


def test_senal_fuerte_recibe_mas_capital_que_debil() -> None:
    """CA-65: la convicción se traduce en tamaño de posición.

    Se usa un stop amplio para que el tope de capital por posición no se
    active y la comparación aísle el efecto de la convicción.
    """
    fuerte = allocate_capital(
        [_senal(fuerza=SignalStrength.STRONG, stop="4500")], Decimal("100000")
    )[0]
    debil = allocate_capital(
        [_senal(fuerza=SignalStrength.WEAK, stop="4500")], Decimal("100000")
    )[0]
    assert fuerte.risk_pct > debil.risk_pct
    assert fuerte.capital_pct > debil.capital_pct


def test_stop_cercano_produce_posicion_mayor() -> None:
    """Propiedad del dimensionamiento por riesgo, no un efecto secundario.

    Con la misma pérdida objetivo, un stop más cercano permite más unidades.
    Por eso existe el tope de capital: sin él, un stop muy apretado
    produciría una posición desproporcionada.
    """
    cercano = allocate_capital(
        [_senal(stop="4950")], Decimal("100000"), max_capital_pct=100.0
    )[0]
    lejano = allocate_capital(
        [_senal(stop="4500")], Decimal("100000"), max_capital_pct=100.0
    )[0]
    assert cercano.units > lejano.units


def test_filtro_por_conviccion_minima() -> None:
    """CA-66: se puede exigir que sólo se operen señales fuertes."""
    señales = [
        _senal(symbol="A", fuerza=SignalStrength.STRONG),
        _senal(symbol="B", fuerza=SignalStrength.MODERATE),
        _senal(symbol="C", fuerza=SignalStrength.WEAK),
    ]
    solo_fuertes = allocate_capital(
        señales, Decimal("100000"), min_strength=SignalStrength.STRONG
    )
    assert [a.symbol for a in solo_fuertes] == ["A"]

    desde_moderada = allocate_capital(
        señales, Decimal("100000"), min_strength=SignalStrength.MODERATE
    )
    assert sorted(a.symbol for a in desde_moderada) == ["A", "B"]


def test_sin_stop_no_se_asigna_capital() -> None:
    """Una señal sin invalidación no tiene riesgo acotado, luego no se opera."""
    ahora = datetime.now(timezone.utc)
    sin_stop = Signal(
        signal_id="x", strategy="s", strategy_version="1.0.0", symbol="US500",
        as_of=ahora, generated_at=ahora, direction=Direction.LONG,
        observed_price=Decimal("5000"), entry_price=Decimal("5000"),
        rationale="sin stop",
    )
    assert allocate_capital([sin_stop], Decimal("100000")) == []


def test_tope_de_capital_por_posicion() -> None:
    """Un stop muy cercano no debe producir una posición desmesurada."""
    apretada = _senal(precio="5000", stop="4999")
    reparto = allocate_capital(
        [apretada], Decimal("100000"), max_capital_pct=20.0
    )
    assert reparto[0].capital_pct <= 20.01
    assert "limitado" in " ".join(reparto[0].reasons)


def test_correlacion_reduce_el_peso() -> None:
    """CA-67: instrumentos casi idénticos no cuentan como diversificación.

    Cinco índices europeos son casi la misma apuesta repetida. Sin esta
    penalización la cartera parecería diversificada y no lo estaría.
    """
    np.random.seed(1)
    base = pd.Series(np.random.normal(0, 0.01, 300),
                     index=pd.bdate_range("2024-01-01", periods=300))
    casi_igual = base + np.random.normal(0, 0.0005, 300)
    distinto = pd.Series(np.random.normal(0, 0.01, 300), index=base.index)

    series = {"A": base, "B": casi_igual, "C": distinto}

    factor_alto, nota = correlation_penalty("B", ["A"], series)
    assert factor_alto < 1.0 and nota is not None

    factor_bajo, sin_nota = correlation_penalty("C", ["A"], series)
    assert factor_bajo == 1.0 and sin_nota is None


def test_puntuacion_se_contrae_con_pocas_operaciones() -> None:
    """CA-68: con histórico corto, todas las estrategias pesan igual.

    Con pocas operaciones el resultado observado no distingue habilidad de
    suerte, y premiarlo sería asignar capital al azar.
    """
    pocas = pd.DataFrame([
        {"Estrategia": "buena", "Sharpe": 2.5, "Operaciones": 4},
        {"Estrategia": "mala", "Sharpe": -1.0, "Operaciones": 4},
    ])
    puntos = score_strategies(pocas)
    assert not puntos["buena"].reliable
    assert abs(puntos["buena"].quality - puntos["mala"].quality) < 0.35


def test_puntuacion_diferencia_con_historico_suficiente() -> None:
    muchas = pd.DataFrame([
        {"Estrategia": "buena", "Sharpe": 2.0,
         "Operaciones": MIN_OPERACIONES_FIABLES * 3},
        {"Estrategia": "mala", "Sharpe": -0.5,
         "Operaciones": MIN_OPERACIONES_FIABLES * 3},
    ])
    puntos = score_strategies(muchas)
    assert puntos["buena"].reliable
    assert puntos["buena"].quality > puntos["mala"].quality


def test_contraccion_cero_iguala_todos_los_pesos() -> None:
    """Con `shrinkage=0` el histórico se ignora por completo."""
    datos = pd.DataFrame([
        {"Estrategia": "buena", "Sharpe": 3.0, "Operaciones": 200},
        {"Estrategia": "mala", "Sharpe": -2.0, "Operaciones": 200},
    ])
    puntos = score_strategies(datos, shrinkage=0.0)
    assert puntos["buena"].quality == pytest.approx(puntos["mala"].quality)


def test_ninguna_estrategia_acapara_el_presupuesto() -> None:
    """Ni con un histórico excelente se concentra todo en una estrategia."""
    datos = pd.DataFrame([
        {"Estrategia": "estrella", "Sharpe": 5.0, "Operaciones": 500},
        {"Estrategia": "normal", "Sharpe": 0.1, "Operaciones": 500},
    ])
    puntos = score_strategies(datos, shrinkage=1.0)
    assert puntos["estrella"].quality <= 1.8


def test_resumen_del_reparto() -> None:
    señales = [_senal(symbol=f"S{i}") for i in range(3)]
    reparto = allocate_capital(señales, Decimal("100000"))
    resumen = allocation_summary(reparto, Decimal("100000"))
    assert resumen["posiciones"] == len(reparto)
    assert resumen["capital_libre_pct"] >= 0
    assert resumen["perdida_maxima"] > 0


def test_reparto_vacio_no_falla() -> None:
    assert allocate_capital([], Decimal("100000")) == []
    assert allocation_summary([], Decimal("100000"))["posiciones"] == 0


def test_importe_asignado_coincide_con_las_unidades() -> None:
    """El importe en dólares debe cuadrar con unidades por precio."""
    a = allocate_capital([_senal()], Decimal("100000"))[0]
    assert abs(a.units * a.entry_price - a.capital_amount) < 0.01


def test_cada_asignacion_explica_su_peso() -> None:
    """CA-69: el operador debe poder auditar por qué recibió ese tamaño."""
    for a in allocate_capital(
        [_senal(fuerza=SignalStrength.STRONG)], Decimal("100000")
    ):
        assert a.reasons
        assert "convicción" in " ".join(a.reasons)
