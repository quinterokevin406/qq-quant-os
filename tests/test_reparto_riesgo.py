"""Pruebas del reparto por riesgo y del cálculo de equity sin bróker.

La prueba central es `test_diversificar_por_riesgo_no_por_capital`: verifica que
cuatro índices altamente correlacionados NO reciben el mismo riesgo que cuatro
activos independientes, que es la diferencia entre repartir por dinero y
repartir por riesgo.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from qq_core.portfolio.account import RiskLimits
from qq_core.portfolio.diversification import (
    AllocationDecision,
    Candidate,
    allocate_by_risk,
    build_clusters,
    effective_independent_bets,
)
from qq_core.portfolio.equity import TrackedPosition, compute_equity

SIMBOLOS = ["US500", "USTEC", "DE40", "DJ30", "XAUUSD", "EURUSD"]

MATRIZ = pd.DataFrame(
    np.array([
        [1.00, 0.93, 0.85, 0.95, 0.10, 0.05],
        [0.93, 1.00, 0.82, 0.90, 0.12, 0.03],
        [0.85, 0.82, 1.00, 0.83, 0.15, 0.20],
        [0.95, 0.90, 0.83, 1.00, 0.08, 0.04],
        [0.10, 0.12, 0.15, 0.08, 1.00, 0.25],
        [0.05, 0.03, 0.20, 0.04, 0.25, 1.00],
    ]),
    index=SIMBOLOS,
    columns=SIMBOLOS,
)


def cand(symbol, quality, times=1, cluster="x"):
    return Candidate(
        signal_id=f"s-{symbol}",
        symbol=symbol,
        strategy="trend",
        direction="long",
        quality_score=quality,
        entry_price=Decimal("100"),
        stop_price=Decimal("98"),
        times_seen=times,
        cluster=cluster,
    )


# --------------------------------------------------------------------------- #
# CA-96 a CA-100: diversificación por riesgo
# --------------------------------------------------------------------------- #


def test_apuestas_efectivas_caen_con_la_correlacion() -> None:
    """CA-96: cinco posiciones correlacionadas no son cinco apuestas.

    Es el concepto que hace que "diversificar por riesgo" sea medible en lugar
    de una expresión vaga.
    """
    n = 5
    alta = pd.DataFrame(
        np.full((n, n), 0.9) + np.eye(n) * 0.1,
        index=[f"s{i}" for i in range(n)],
        columns=[f"s{i}" for i in range(n)],
    )
    baja = pd.DataFrame(
        np.full((n, n), 0.1) + np.eye(n) * 0.9,
        index=[f"s{i}" for i in range(n)],
        columns=[f"s{i}" for i in range(n)],
    )
    simbolos = [f"s{i}" for i in range(n)]

    correlacionadas = effective_independent_bets(simbolos, alta)
    independientes = effective_independent_bets(simbolos, baja)

    assert correlacionadas < 1.5
    assert independientes > 3.0
    assert correlacionadas < independientes


def test_los_indices_caen_en_un_solo_grupo() -> None:
    """CA-97: los instrumentos que se mueven juntos se agrupan.

    Cuatro índices bursátiles son una apuesta con cuatro nombres. El motor debe
    reconocerlo para no tratarlos como cuatro riesgos separados.
    """
    grupos = build_clusters(MATRIZ)

    indices = {grupos["US500"], grupos["USTEC"], grupos["DE40"], grupos["DJ30"]}
    assert len(indices) == 1, "los cuatro índices deben compartir grupo"
    assert grupos["EURUSD"] != grupos["US500"]


def test_diversificar_por_riesgo_no_por_capital() -> None:
    """CA-98: LA PRUEBA CENTRAL del motor de reparto.

    Con cuatro índices correlacionados y dos activos independientes, el segundo
    índice debe recibir MENOS riesgo que el primero, pese a tener calidad
    prácticamente igual. Repartir por capital les daría lo mismo.
    """
    grupos = build_clusters(MATRIZ)
    candidatos = [
        cand("US500", 85, 5, grupos["US500"]),
        cand("USTEC", 83, 4, grupos["USTEC"]),
        cand("EURUSD", 68, 6, grupos["EURUSD"]),
    ]

    plan = allocate_by_risk(
        candidatos, Decimal("100000"), RiskLimits().validated(), correlations=MATRIZ
    )

    por_simbolo = {a.candidate.symbol: a for a in plan.allocations}
    assert por_simbolo["USTEC"].risk_pct < por_simbolo["US500"].risk_pct
    assert por_simbolo["USTEC"].correlation_penalty > 0.8


def test_una_senal_peor_puede_adelantar_si_diversifica() -> None:
    """CA-99: no siempre gana la de mayor calidad.

    Era un requisito explícito: una señal que mejora la estructura de riesgo
    puede recibir prioridad sobre otra con mejor puntuación que duplica
    exposición existente.
    """
    grupos = build_clusters(MATRIZ)
    candidatos = [
        cand("US500", 85, 5, grupos["US500"]),
        cand("DE40", 80, 3, grupos["DE40"]),
        cand("EURUSD", 68, 6, grupos["EURUSD"]),
    ]

    plan = allocate_by_risk(
        candidatos, Decimal("100000"), RiskLimits().validated(), correlations=MATRIZ
    )
    orden = [a.candidate.symbol for a in plan.allocations]

    # EURUSD, con la peor calidad, adelanta a DE40 por diversificación.
    assert orden.index("EURUSD") < orden.index("DE40")


def test_correlacion_casi_perfecta_se_rechaza() -> None:
    """CA-100: añadir la misma apuesta con otro nombre no es diversificar."""
    grupos = build_clusters(MATRIZ)
    plan = allocate_by_risk(
        [cand("US500", 85, 5, grupos["US500"]), cand("DJ30", 84, 4, grupos["DJ30"])],
        Decimal("100000"),
        RiskLimits().validated(),
        correlations=MATRIZ,
    )
    dj = next(a for a in plan.allocations if a.candidate.symbol == "DJ30")
    assert dj.decision is AllocationDecision.REJECT_CORRELATION


# --------------------------------------------------------------------------- #
# CA-101 a CA-103: reserva para señales futuras
# --------------------------------------------------------------------------- #


def test_se_reserva_capacidad_para_manana() -> None:
    """CA-101: el motor no gasta todo el presupuesto hoy.

    Era la preocupación planteada de forma literal: si mañana aparece una
    operación mejor, ¿de dónde sale el capital? De la reserva.
    """
    limites = RiskLimits().validated()
    candidatos = [cand(f"S{i}", 90 - i) for i in range(6)]

    plan = allocate_by_risk(candidatos, Decimal("100000"), limites)

    assert plan.reserved_pct > 0
    assert plan.used_pct <= plan.deployable_pct
    assert plan.used_pct < limites.max_total_open_risk_pct


def test_agotado_el_presupuesto_las_siguientes_esperan() -> None:
    """CA-102: al llenarse la capacidad, se espera en lugar de forzar.

    Cada candidata va en su propio grupo para aislar el mecanismo de reserva:
    si compartieran grupo se agotaría antes el techo por cluster, que es un
    límite distinto y se verifica en CA-103.
    """
    limites = RiskLimits().validated()
    candidatos = [cand(f"S{i}", 90, 1, f"grupo_{i}") for i in range(20)]

    plan = allocate_by_risk(candidatos, Decimal("100000"), limites)

    esperando = [
        a for a in plan.allocations
        if a.decision is AllocationDecision.WAIT_FOR_CAPACITY
    ]
    limitadas = [a for a in plan.allocations if not a.decision.is_accept]

    assert limitadas, "con 20 candidatas alguna debe quedar fuera"
    assert esperando, "el presupuesto desplegable debe agotarse antes que las 20"
    # Y lo importante: la reserva permanece intacta para señales futuras.
    assert plan.used_pct <= plan.deployable_pct
    assert plan.reserved_pct > 0


def test_el_grupo_correlacionado_tiene_techo_propio() -> None:
    """CA-103: un grupo no puede absorber todo el presupuesto.

    Con trece índices en el universo, sin este techo el grupo de renta variable
    se llevaría la cartera entera.
    """
    limites = RiskLimits().validated()
    candidatos = [cand(f"IDX{i}", 85, 1, "grupo_indices") for i in range(10)]

    plan = allocate_by_risk(candidatos, Decimal("100000"), limites)
    total_grupo = sum(
        a.risk_pct for a in plan.allocations if a.decision.is_accept
    )

    assert total_grupo <= limites.max_risk_per_cluster_pct


# --------------------------------------------------------------------------- #
# CA-104 a CA-107: equity sin bróker
# --------------------------------------------------------------------------- #


def test_equity_se_calcula_desde_capital_propio_y_seguimiento() -> None:
    """CA-104: no hace falta conectarse a ningún bróker.

    Equity = capital declarado + resultado cerrado + resultado flotante.
    """
    posiciones = [
        TrackedPosition("a", "US500", "trend", "long", Decimal("100"),
                        Decimal("110"), Decimal("95"), Decimal("10")),
        TrackedPosition("b", "EURUSD", "mr", "short", Decimal("1.10"),
                        Decimal("1.05"), Decimal("1.13"), Decimal("1000")),
        TrackedPosition("c", "XAUUSD", "trend", "long", Decimal("2000"),
                        Decimal("2100"), None, Decimal("1"),
                        is_closed=True, close_price=Decimal("2100")),
    ]

    e = compute_equity(Decimal("10000"), posiciones)

    assert e.realized_pnl == Decimal("100")
    assert e.floating_pnl == Decimal("150")
    assert e.equity == Decimal("10250")
    assert e.n_open == 2
    assert e.n_closed == 1


def test_una_posicion_grande_no_es_riesgo_grande() -> None:
    """CA-105: capital y riesgo son cosas distintas.

    Una posición de 10.000 con el stop a un 2% arriesga 200, no 10.000. Es la
    confusión que la especificación pedía expresamente evitar.
    """
    p = TrackedPosition("a", "US500", "trend", "long", Decimal("100"),
                        Decimal("100"), Decimal("98"), Decimal("100"))
    e = compute_equity(Decimal("50000"), [p])

    assert e.gross_exposure == Decimal("10000")
    assert e.open_risk == Decimal("200")
    assert e.open_risk < e.gross_exposure / 10


def test_riesgo_sin_stop_se_declara_no_se_cuenta_como_cero() -> None:
    """CA-106: un riesgo desconocido no puede parecer capacidad libre.

    Mismo principio que rige el puerto de cuenta: lo que no se sabe se declara.
    """
    posiciones = [
        TrackedPosition("a", "US500", "trend", "long", Decimal("100"),
                        Decimal("100"), Decimal("98"), Decimal("10")),
        TrackedPosition("b", "DE40", "trend", "long", Decimal("100"),
                        Decimal("100"), None, Decimal("10")),
    ]
    e = compute_equity(Decimal("10000"), posiciones)

    assert not e.risk_is_complete
    assert "b" in e.positions_without_stop


def test_capital_inicial_no_positivo_falla() -> None:
    """CA-107: sin capital declarado no hay nada que calcular."""
    with pytest.raises(ValueError):
        compute_equity(Decimal("0"), [])
