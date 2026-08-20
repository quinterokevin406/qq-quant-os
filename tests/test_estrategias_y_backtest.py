"""Pruebas de indicadores, estrategias, señales y motor de backtesting.

La prueba más importante del archivo es `test_sin_sesgo_de_anticipacion`:
verifica automáticamente que ningún indicador usa información futura. Es el
error que arruina proyectos de investigación cuantitativa, y el único que no
se detecta por inspección visual del código.
"""

from __future__ import annotations

from datetime import timezone

import numpy as np
import pandas as pd
import pytest

from qq_core.backtest.engine import BacktestConfig, compute_metrics, run_backtest
from qq_core.catalog import instruments as catalog
from qq_core.domain.instrument import CFD
from qq_core.domain.signal import Direction, Signal, SignalStrength
from qq_core.features import engine as fx
from qq_core.strategies.library import build_registry, MeanReversion, TrendFollowing

REGISTRY = build_registry()


@pytest.fixture
def precios() -> pd.DataFrame:
    """Serie sintética con tendencia y ruido, reproducible."""
    np.random.seed(11)
    n = 1200
    fechas = pd.bdate_range("2020-01-01", periods=n)
    px = 3000 * np.exp(
        np.linspace(0, 0.5, n) + np.cumsum(np.random.normal(0, 0.01, n))
    )
    return pd.DataFrame(
        {
            "open": px * 0.999,
            "high": px * 1.004,
            "low": px * 0.996,
            "close": px,
            "volume": np.random.randint(100_000, 900_000, n).astype(float),
        },
        index=fechas,
    )


# --------------------------------------------------------------------- #
# Indicadores — protección contra sesgo de anticipación
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "nombre,calculo",
    [
        ("sma_20", lambda s: fx.sma(s, 20)),
        ("sma_200", lambda s: fx.sma(s, 200)),
        ("ema_50", lambda s: fx.ema(s, 50)),
        ("volatilidad", lambda s: fx.realized_volatility(s, 20)),
        ("zscore", lambda s: fx.zscore(s, 20)),
        ("retornos", lambda s: fx.returns(s, 5)),
    ],
)
def test_sin_sesgo_de_anticipacion(precios, nombre, calculo) -> None:
    """CA-27: ningún indicador cambia al conocerse datos futuros.

    Se recalcula cada indicador truncando la serie en varios puntos y se
    comprueba que el valor coincide con el de la serie completa. Cualquier
    diferencia significa que el indicador mira hacia adelante.
    """
    fx.assert_no_lookahead(calculo, precios["close"])


def test_sma_no_rellena_valores_iniciales(precios) -> None:
    """Los primeros valores son NaN a propósito: rellenarlos inventaría señal."""
    resultado = fx.sma(precios["close"], 20)
    assert resultado.iloc[:19].isna().all()
    assert not pd.isna(resultado.iloc[19])


def test_atr_es_positivo(precios) -> None:
    valores = fx.atr(precios["high"], precios["low"], precios["close"], 14).dropna()
    assert (valores > 0).all()


def test_drawdown_nunca_positivo(precios) -> None:
    dd = fx.drawdown(precios["close"])
    assert dd.max() <= 1e-12


def test_bars_to_frame_con_lista_vacia() -> None:
    frame = fx.bars_to_frame([])
    assert frame.empty
    assert "close" in frame.columns


# --------------------------------------------------------------------- #
# Estrategias
# --------------------------------------------------------------------- #


def test_todas_las_estrategias_cumplen_el_contrato(precios) -> None:
    """CA-28: formato uniforme de salida en las nueve estrategias.

    Es lo que permite que el portafolio y el panel funcionen con cualquier
    estrategia sin conocerla. Verificarlo sobre TODAS es lo que impide que una
    nueva se desvíe del contrato sin que nadie lo note.
    """
    for nombre, estrategia in REGISTRY.items():
        try:
            posiciones = estrategia.target_position(precios, "TEST")
        except TypeError:
            posiciones = estrategia.target_position(precios)
        assert isinstance(posiciones, pd.Series), nombre
        assert len(posiciones) == len(precios), nombre
        assert posiciones.isin([-1.0, 0.0, 1.0]).all(), nombre

        if len(precios) < estrategia.warmup_bars + 5:
            continue
        señal = estrategia.explain(precios, "TEST", "Instrumento de prueba")
        assert isinstance(señal, Signal), nombre
        assert señal.strategy == nombre
        assert señal.rationale
        assert señal.horizon is not None
        assert señal.observed_price > 0
        assert señal.entry_price > 0


def test_toda_senal_accionable_lleva_stop(precios) -> None:
    """CA-42: sin nivel de invalidación no se puede dimensionar la posición."""
    for nombre, estrategia in REGISTRY.items():
        if len(precios) < estrategia.warmup_bars + 5:
            continue
        s = estrategia.explain(precios, "TEST")
        if s and s.is_actionable:
            assert s.stop_price is not None, f"{nombre} sin invalidación"
            assert s.target_price is not None, f"{nombre} sin objetivo"


def test_niveles_estan_del_lado_correcto(precios) -> None:
    """CA-43: en compras el stop va debajo y el objetivo arriba; al revés en ventas.

    Invertirlos cambia el signo del riesgo y hace que el dimensionamiento
    calcule tamaños absurdos.
    """
    from qq_core.domain.signal import Direction as D

    for nombre, estrategia in REGISTRY.items():
        if len(precios) < estrategia.warmup_bars + 5:
            continue
        s = estrategia.explain(precios, "TEST")
        if not s or not s.is_actionable:
            continue
        if s.direction is D.LONG:
            assert s.stop_price < s.entry_price < s.target_price, nombre
        else:
            assert s.target_price < s.entry_price < s.stop_price, nombre


def test_cada_estrategia_declara_horizonte() -> None:
    """El operador necesita saber cuánto durará la operación."""
    horizontes = {e.horizon for e in REGISTRY.values()}
    assert len(horizontes) >= 3, "las estrategias deben cubrir varios horizontes"


def test_hay_al_menos_nueve_estrategias() -> None:
    """CA-44: el plan original pedía quince; nueve están operativas."""
    assert len(REGISTRY) >= 9


def test_estrategia_no_opera_durante_calentamiento(precios) -> None:
    """Sin datos suficientes, la posición es cero, no una apuesta a ciegas."""
    estrategia = TrendFollowing(fast=50, slow=200)
    posiciones = estrategia.target_position(precios)
    assert (posiciones.iloc[: estrategia.slow - 1] == 0).all()


def test_trend_following_sigue_la_tendencia() -> None:
    """En una serie alcista sostenida, la posición debe ser comprada."""
    n = 600
    fechas = pd.bdate_range("2021-01-01", periods=n)
    px = np.linspace(100, 300, n)
    df = pd.DataFrame(
        {"open": px, "high": px * 1.01, "low": px * 0.99, "close": px}, index=fechas
    )
    posiciones = TrendFollowing(fast=20, slow=50).target_position(df)
    assert posiciones.iloc[-1] == 1.0


def test_trend_following_detecta_tendencia_bajista() -> None:
    n = 600
    fechas = pd.bdate_range("2021-01-01", periods=n)
    px = np.linspace(300, 100, n)
    df = pd.DataFrame(
        {"open": px, "high": px * 1.01, "low": px * 0.99, "close": px}, index=fechas
    )
    posiciones = TrendFollowing(fast=20, slow=50).target_position(df)
    assert posiciones.iloc[-1] == -1.0


def test_media_rapida_debe_ser_menor_que_lenta() -> None:
    with pytest.raises(ValueError):
        TrendFollowing(fast=200, slow=50)


def test_mean_reversion_mantiene_estado(precios) -> None:
    """La posición persiste hasta cruzar el umbral de salida, no oscila cada día."""
    posiciones = MeanReversion().target_position(precios)
    cambios = (posiciones.diff() != 0).sum()
    assert cambios < len(posiciones) * 0.25


def test_senal_explica_su_razon(precios) -> None:
    """CA-29: toda señal es auditable.

    Sin razón registrada, el Módulo 15 no puede reconstruir por qué se tomó
    una decisión de inversión.
    """
    señal = TrendFollowing().explain(precios, "US500")
    assert señal is not None
    assert len(señal.rationale) > 30
    assert señal.features
    assert señal.as_of.tzinfo is timezone.utc


def test_senal_id_es_determinista(precios) -> None:
    """Regenerar señales no debe duplicar el registro de decisiones."""
    a = TrendFollowing().explain(precios, "US500")
    b = TrendFollowing().explain(precios, "US500")
    assert a and b and a.signal_id == b.signal_id


def test_senal_es_inmutable(precios) -> None:
    from pydantic import ValidationError

    señal = TrendFollowing().explain(precios, "US500")
    assert señal is not None
    with pytest.raises(ValidationError):
        señal.direction = Direction.SHORT  # type: ignore[misc]


def test_flat_no_es_accionable(precios) -> None:
    señal = TrendFollowing().explain(precios, "US500")
    assert señal is not None
    assert señal.is_actionable == (señal.direction is not Direction.FLAT)


# --------------------------------------------------------------------- #
# Motor de backtesting
# --------------------------------------------------------------------- #


def _financing():
    inst = catalog.get("US500").instrument
    assert isinstance(inst, CFD)
    return inst.financing


def test_backtest_aplica_desfase_de_ejecucion(precios) -> None:
    """CA-30: LA PRUEBA MÁS IMPORTANTE DEL MOTOR.

    La señal calculada con el cierre de `t` se ejecuta a partir de `t+1`.
    Si el motor no aplicara este desfase, el backtest usaría el precio de
    cierre para decidir una operación ejecutada ese mismo día, produciendo
    resultados excelentes e irreproducibles.
    """
    estrategia = TrendFollowing()
    señal = estrategia.target_position(precios)
    resultado = run_backtest(precios, estrategia, "TEST", financing=None)

    esperado = señal.shift(1).fillna(0.0)
    # La exposición es la posición desplazada, escalada por volatilidad:
    # el signo debe coincidir siempre con la señal del día anterior.
    signos_ok = (
        np.sign(resultado.positions.values) == np.sign(esperado.values)
    ) | (resultado.positions.values == 0)
    assert signos_ok.all()


def test_backtest_produce_curva_de_capital(precios) -> None:
    r = run_backtest(precios, TrendFollowing(), "US500", financing=_financing())
    assert len(r.equity) == len(precios)
    assert (r.equity > 0).all()
    assert r.metrics


def test_coste_de_financiacion_reduce_rentabilidad(precios) -> None:
    """CA-31: el coste de mantener posiciones debe empeorar el resultado.

    Si activar el coste no cambiara nada, significaría que no se está
    aplicando, y todos los backtests serían optimistas.
    """
    con = run_backtest(precios, TrendFollowing(), "US500", financing=_financing())
    sin = run_backtest(
        precios, TrendFollowing(), "US500", financing=_financing(),
        config=BacktestConfig(apply_financing=False),
    )
    assert con.equity.iloc[-1] < sin.equity.iloc[-1]
    assert con.costs["financing_total_pct"] != 0


def test_coste_de_transaccion_se_aplica(precios) -> None:
    caro = run_backtest(
        precios, TrendFollowing(), "US500", financing=None,
        config=BacktestConfig(spread_bps=50.0),
    )
    barato = run_backtest(
        precios, TrendFollowing(), "US500", financing=None,
        config=BacktestConfig(spread_bps=0.0),
    )
    assert caro.equity.iloc[-1] < barato.equity.iloc[-1]


def test_operaciones_se_cuentan_sobre_posicion_discreta(precios) -> None:
    """El escalado por volatilidad cambia a diario; eso no es una operación.

    Contarlo como tal inflaría el número de operaciones a una por sesión y
    haría irreconocibles los costes reales.
    """
    # Sin política de salida: la estrategia base opera pocas veces.
    r = run_backtest(
        precios, TrendFollowing(), "US500", financing=_financing(),
        config=BacktestConfig(use_exit_policy=False),
    )
    assert r.metrics["trades"] < len(precios) * 0.1

    # Con trailing stop hay más operaciones —cada stop cierra y se vuelve a
    # entrar— pero sigue muy lejos de una por sesión.
    con_trailing = run_backtest(precios, TrendFollowing(), "US500",
                                financing=_financing())
    assert con_trailing.metrics["trades"] < len(precios) * 0.35


def test_backtest_rechaza_datos_insuficientes() -> None:
    corto = pd.DataFrame(
        {"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2]},
        index=pd.bdate_range("2024-01-01", periods=2),
    )
    with pytest.raises(ValueError, match="insuficientes"):
        run_backtest(corto, TrendFollowing(), "X")


def test_backtest_rechaza_columnas_faltantes(precios) -> None:
    with pytest.raises(ValueError, match="faltan columnas"):
        run_backtest(precios[["close"]], TrendFollowing(), "X")


def test_metricas_con_serie_vacia() -> None:
    assert compute_metrics(pd.Series(dtype=float), pd.Series(dtype=float)) == {}


def test_caida_maxima_es_negativa_o_cero(precios) -> None:
    r = run_backtest(precios, TrendFollowing(), "US500", financing=_financing())
    assert r.metrics["max_drawdown"] <= 0


def test_apalancamiento_respeta_el_limite(precios) -> None:
    """El dimensionamiento por volatilidad no puede saltarse el límite."""
    cfg = BacktestConfig(max_leverage=2.0)
    r = run_backtest(precios, TrendFollowing(), "US500", financing=None, config=cfg)
    assert r.positions.abs().max() <= 2.0 + 1e-9


def test_apalancamiento_por_defecto_es_uno() -> None:
    """CA-35: sin apalancamiento por defecto.

    En instrumentos con coste de financiación, apalancar multiplica ese coste
    por el mismo factor. Un backtest de diez años con 3x convierte cualquier
    estrategia en pérdida garantizada. Subirlo debe ser una decisión explícita
    y justificada, nunca un valor heredado por descuido.
    """
    assert BacktestConfig().max_leverage == 1.0


# --------------------------------------------------------------------- #
# Contrato de señal v2: plan de operación completo
# --------------------------------------------------------------------- #


def test_toda_senal_accionable_lleva_los_tres_precios(precios) -> None:
    """CA-45: entrada, objetivo e invalidación, los tres explícitos.

    El operador no puede ejecutar una recomendación que sólo diga la
    dirección. Necesita saber a qué precio entrar, dónde tomar beneficio y
    dónde reconocer que la hipótesis era errónea.
    """
    for estrategia in REGISTRY.values():
        if len(precios) < estrategia.warmup_bars + 5:
            continue
        señal = estrategia.explain(precios, "TEST", "Prueba")
        if señal is None or not señal.is_actionable:
            continue
        assert señal.entry_price > 0, estrategia.name
        assert señal.target_price is not None, estrategia.name
        assert señal.stop_price is not None, estrategia.name


def test_invalidacion_esta_del_lado_correcto(precios) -> None:
    """CA-46: en compras el stop va por debajo; en ventas, por encima.

    Un stop del lado equivocado se ejecutaría de inmediato y convertiría cada
    operación en una pérdida instantánea.
    """
    from qq_core.domain.signal import Direction

    for estrategia in REGISTRY.values():
        if len(precios) < estrategia.warmup_bars + 5:
            continue
        s = estrategia.explain(precios, "TEST", "Prueba")
        if s is None or not s.is_actionable or s.stop_price is None:
            continue
        if s.direction is Direction.LONG:
            assert s.stop_price < s.entry_price, estrategia.name
            assert s.target_price > s.entry_price, estrategia.name
        else:
            assert s.stop_price > s.entry_price, estrategia.name
            assert s.target_price < s.entry_price, estrategia.name


def test_relacion_beneficio_riesgo_es_favorable(precios) -> None:
    """El objetivo debe estar más lejos que la invalidación.

    Si se arriesga más de lo que se puede ganar, la estrategia necesita
    acertar más de la mitad de las veces sólo para no perder dinero.
    """
    for estrategia in REGISTRY.values():
        if len(precios) < estrategia.warmup_bars + 5:
            continue
        s = estrategia.explain(precios, "TEST", "Prueba")
        if s is None or s.risk_reward is None:
            continue
        assert s.risk_reward >= 1.0, f"{estrategia.name}: R/R {s.risk_reward}"


def test_horizonte_determina_coste_de_financiacion() -> None:
    """CA-47: una operación intradía no paga swap; una de un mes, sí.

    Es lo que permite al operador saber, antes de abrir, cuánto le costará
    mantener la posición el tiempo previsto.
    """
    from decimal import Decimal as D

    from qq_core.domain.signal import Horizon

    assert Horizon.INTRADAY.pays_financing is False
    assert Horizon.MONTH_1.pays_financing is True
    assert Horizon.MONTH_1.expected_days > Horizon.WEEK_1.expected_days


def test_estimacion_de_coste_por_horizonte(precios) -> None:
    """El coste estimado crece con el tiempo de mantenimiento."""
    from decimal import Decimal as D

    s = TrendFollowing().explain(precios, "US500", "S&P 500")
    assert s is not None
    coste = s.financing_cost_estimate(D("-0.1336"))
    assert coste is not None and coste < 0


def test_cada_estrategia_declara_descripcion() -> None:
    """El panel muestra qué hace cada estrategia en lenguaje del operador."""
    for estrategia in REGISTRY.values():
        assert estrategia.description, estrategia.name
        assert estrategia.exit_rule, estrategia.name
        assert estrategia.label, estrategia.name


def test_estrategias_bloqueadas_documentadas() -> None:
    """CA-48: lo que falta del plan original está justificado, no olvidado."""
    from qq_core.strategies.library import BLOCKED_STRATEGIES

    assert len(BLOCKED_STRATEGIES) >= 5
    for nombre, motivo in BLOCKED_STRATEGIES.items():
        assert len(motivo) > 20, nombre


# --------------------------------------------------------------------- #
# Gestión de salidas: trailing stop
# --------------------------------------------------------------------- #


def test_trailing_stop_nunca_retrocede() -> None:
    """CA-61: el stop sólo se mueve a favor de la posición.

    Es la propiedad que define un trailing stop. Si pudiera alejarse cuando
    el precio se gira, no protegería nada.
    """
    from decimal import Decimal as D

    from qq_core.backtest.exits import ExitPolicy, trailing_stop_level
    from qq_core.domain.signal import Direction

    politica = ExitPolicy()
    entrada = D("100")
    atr = D("2")

    subiendo = trailing_stop_level(entrada, D("110"), atr, Direction.LONG, politica)
    mas_arriba = trailing_stop_level(entrada, D("120"), atr, Direction.LONG, politica)
    assert mas_arriba > subiendo


def test_trailing_reduce_la_caida_maxima(precios) -> None:
    """CA-62: dejar correr las ganancias reduce lo que se devuelve.

    Es la mejora estructural que aporta, frente a ajustar parámetros hasta
    que el backtest salga favorable, que sería sobreajuste.
    """
    sin = run_backtest(
        precios, TrendFollowing(), "US500", financing=_financing(),
        config=BacktestConfig(use_exit_policy=False),
    )
    con = run_backtest(precios, TrendFollowing(), "US500", financing=_financing())
    assert con.metrics["max_drawdown"] > sin.metrics["max_drawdown"]


def test_stop_se_comprueba_contra_el_minimo_de_la_sesion() -> None:
    """El stop se activa si el precio lo tocó durante el día.

    Comprobarlo sólo contra el cierre ignoraría que el precio pasó por ese
    nivel y produciría un backtest optimista.
    """
    import numpy as np

    from qq_core.backtest.exits import ExitPolicy, apply_exit_policy

    fechas = pd.bdate_range("2024-01-01", periods=6)
    df = pd.DataFrame(
        {
            "open": [100, 100, 100, 100, 100, 100],
            "high": [101, 101, 101, 101, 101, 101],
            # El día 3 el mínimo perfora el stop aunque el cierre no.
            "low": [99, 99, 90, 99, 99, 99],
            "close": [100, 100, 100, 100, 100, 100],
        },
        index=fechas, dtype=float,
    )
    posiciones = pd.Series([0.0, 1.0, 1.0, 1.0, 1.0, 1.0], index=fechas)
    atr = pd.Series([2.0] * 6, index=fechas)

    efectiva, motivos = apply_exit_policy(df, posiciones, atr, ExitPolicy())
    assert efectiva.iloc[2] == 0.0
    assert motivos.iloc[2] == "stop"


def test_atribucion_explica_el_resultado(precios) -> None:
    """CA-63: el backtest dice POR QUÉ gana o pierde, no sólo cuánto."""
    r = run_backtest(precios, TrendFollowing(), "US500", financing=_financing())
    assert r.attribution is not None
    assert r.attribution.diagnostico
    assert all(len(nota) > 30 for nota in r.attribution.diagnostico)

    filas = r.attribution.to_rows()
    assert len(filas) == 4
    assert any("NETO" in f["Concepto"] for f in filas)


def test_atribucion_descompone_bruto_menos_costes(precios) -> None:
    """La suma de las partes debe dar el total. Si no, falta algún coste."""
    r = run_backtest(precios, TrendFollowing(), "US500", financing=_financing())
    a = r.attribution
    esperado = (
        a.resultado_bruto_pct + a.coste_financiacion_pct + a.coste_transaccion_pct
    )
    assert abs(esperado - a.resultado_neto_pct) < 0.01
