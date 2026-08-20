"""Pruebas de la lista de seguimiento y del filtro por convicción.

Ambas cosas responden a la misma necesidad del operador: saber si lo que
recomendó el sistema sigue siendo válido, y poder simular únicamente las
señales en que el sistema tenía más confianza.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from qq_core.domain.signal import Direction, Horizon, Signal, SignalStrength
from qq_core.signals.tracking import SignalHealth, track_signal
from qq_core.signals.watchlist import Watchlist, watch_id
from qq_core.strategies.library import build_registry


def _senal(fuerza=SignalStrength.STRONG, symbol="US500") -> Signal:
    ahora = datetime.now(timezone.utc)
    return Signal(
        signal_id=f"tf:{symbol}", strategy="trend_following",
        strategy_label="Seguimiento de tendencia", strategy_version="2.0.0",
        symbol=symbol, as_of=ahora, generated_at=ahora,
        direction=Direction.LONG, strength=fuerza, horizon=Horizon.MONTH_1,
        observed_price=Decimal("5000"), entry_price=Decimal("5000"),
        target_price=Decimal("5300"), stop_price=Decimal("4900"),
        rationale="prueba",
    )


@pytest.fixture
def lista(tmp_path) -> Watchlist:
    return Watchlist(tmp_path / "seguimiento.db")


# --------------------------------------------------------------------- #
# Persistencia de señales
# --------------------------------------------------------------------- #


def test_la_senal_se_guarda_y_recupera_intacta(lista: Watchlist) -> None:
    """CA-72: se guarda la señal COMPLETA, no sólo el símbolo.

    Sin conocer cómo era la señal el día que se marcó, sería imposible decir
    si hoy se ha debilitado. Este test protege contra guardar sólo una
    referencia.
    """
    original = _senal()
    lista.add(original, "entré con 2 lotes")

    entradas = lista.active_entries()
    assert len(entradas) == 1

    _, recuperada, nota, _ = entradas[0]
    assert recuperada.symbol == original.symbol
    assert recuperada.strength is original.strength
    assert recuperada.entry_price == original.entry_price
    assert recuperada.target_price == original.target_price
    assert nota == "entré con 2 lotes"


def test_la_senal_es_serializable() -> None:
    """CA-73: una señal debe poder guardarse y volver a leerse.

    Falló en su día porque `risk_reward` estaba declarado como campo
    calculado: se escribía al serializar y se rechazaba al leer, con
    `extra="forbid"`. El seguimiento quedaba vacío sin dar error.
    """
    original = _senal()
    copia = Signal.model_validate_json(original.model_dump_json())
    assert copia.symbol == original.symbol
    assert copia.risk_reward == original.risk_reward


def test_retirar_no_borra_el_historico(lista: Watchlist) -> None:
    """Qué se siguió y durante cuánto forma parte del registro."""
    identificador = lista.add(_senal())
    lista.remove(identificador)

    assert lista.count() == 0
    fila = lista._conn.execute(
        "SELECT active, closed_on FROM signal_watch WHERE watch_id=?",
        (identificador,),
    ).fetchone()
    assert fila["active"] == 0
    assert fila["closed_on"] is not None


def test_no_se_duplica_la_misma_senal(lista: Watchlist) -> None:
    hoy = date.today()
    lista.add(_senal(), added=hoy)
    lista.add(_senal(), added=hoy)
    assert lista.count() == 1


def test_misma_senal_en_fechas_distintas_coexiste(lista: Watchlist) -> None:
    """Se puede seguir el mismo instrumento en dos entradas distintas."""
    lista.add(_senal(), added=date(2026, 1, 5))
    lista.add(_senal(), added=date(2026, 3, 20))
    assert lista.count() == 2


def test_detecta_si_ya_esta_en_seguimiento(lista: Watchlist) -> None:
    assert not lista.is_watched("US500", "trend_following")
    lista.add(_senal())
    assert lista.is_watched("US500", "trend_following")


def test_identificador_incluye_la_fecha() -> None:
    a = watch_id("US500", "trend_following", date(2026, 1, 1))
    b = watch_id("US500", "trend_following", date(2026, 6, 1))
    assert a != b


def test_entrada_corrupta_no_rompe_el_resto(lista: Watchlist) -> None:
    """Una señal guardada con un esquema antiguo no debe tumbar la vista."""
    lista.add(_senal())
    lista._conn.execute(
        """INSERT INTO signal_watch
           (watch_id, symbol, strategy, added_on, signal_json, note, active)
           VALUES ('roto','X','y','2026-01-01','{no es json',' ',1)"""
    )
    assert len(lista.active_entries()) == 1


# --------------------------------------------------------------------- #
# Seguimiento sobre lo guardado
# --------------------------------------------------------------------- #


def test_seguimiento_detecta_debilitamiento(lista: Watchlist) -> None:
    """CA-74: el flujo completo, de marcar la señal a ver que se debilita."""
    lista.add(_senal(SignalStrength.STRONG), added=date.today() - timedelta(days=5))
    _, original, _, _ = lista.active_entries()[0]

    estado = track_signal(original, _senal(SignalStrength.WEAK), Decimal("5100"))
    assert estado.health is SignalHealth.WEAKENING
    assert estado.unrealized_pct > 0


# --------------------------------------------------------------------- #
# Convicción histórica y filtro de simulación
# --------------------------------------------------------------------- #


@pytest.fixture
def precios() -> pd.DataFrame:
    np.random.seed(17)
    n = 1400
    fechas = pd.bdate_range("2020-01-01", periods=n)
    px = 5000 * np.exp(
        np.cumsum(np.random.normal(0.06 / 252, 0.16 / np.sqrt(252), n))
    )
    return pd.DataFrame(
        {"open": px * 0.999, "high": px * 1.006, "low": px * 0.994, "close": px},
        index=fechas,
    )


def test_toda_estrategia_declara_conviccion_historica(precios) -> None:
    """CA-75: se puede saber cómo de fuerte era una señal en el pasado.

    Es lo que permite simular «sólo las señales fuertes». Sin este cálculo
    habría que suponer que todas eran iguales.
    """
    for estrategia in build_registry().values():
        serie = estrategia.strength_series(precios)
        assert len(serie) == len(precios)
        assert set(serie.unique()) <= {"strong", "moderate", "weak"}


def test_conviccion_historica_coincide_con_la_de_hoy(precios) -> None:
    """La convicción del último día debe coincidir con la de `explain`.

    Si divergieran, la simulación filtraría por un criterio distinto del que
    se muestra al operador.
    """
    for estrategia in build_registry().values():
        if estrategia.strength_metric(precios) is None:
            continue
        señal = estrategia.explain(precios, "TEST", "Prueba")
        if señal is None or not señal.is_actionable:
            continue
        serie = estrategia.strength_series(precios)
        assert serie.iloc[-1] == señal.strength.value, estrategia.name


def test_filtro_de_conviccion_reduce_operaciones() -> None:
    """CA-76: exigir señales fuertes reduce la actividad.

    Si no la redujera, el filtro no estaría aplicándose.
    """
    from qq_core.catalog import instruments as catalogo
    from qq_core.portfolio.simulator import simulate_portfolio

    np.random.seed(23)
    precios_sim, instrumentos = {}, {}
    for entry in list(catalogo.CATALOG)[:5]:
        n = 1200
        fechas = pd.bdate_range("2021-01-01", periods=n)
        px = 5000 * np.exp(
            np.cumsum(np.random.normal(0.06 / 252, 0.17 / np.sqrt(252), n))
        )
        precios_sim[entry.symbol] = pd.DataFrame(
            {"open": px * 0.999, "high": px * 1.005,
             "low": px * 0.995, "close": px},
            index=fechas,
        )
        instrumentos[entry.symbol] = entry.instrument

    registro = build_registry()
    todas = simulate_portfolio(precios_sim, instrumentos, registro)
    fuertes = simulate_portfolio(
        precios_sim, instrumentos, registro, min_strength="strong"
    )
    assert fuertes.metrics["trades"] < todas.metrics["trades"]


def test_nivel_desconocido_no_rompe() -> None:
    """Un valor inesperado no debe tumbar la simulación."""
    from qq_core.portfolio.simulator import _NIVELES_PERMITIDOS

    assert set(_NIVELES_PERMITIDOS) == {"strong", "moderate", "weak"}
    assert _NIVELES_PERMITIDOS["strong"] == ("strong",)
    assert "weak" in _NIVELES_PERMITIDOS["weak"]
