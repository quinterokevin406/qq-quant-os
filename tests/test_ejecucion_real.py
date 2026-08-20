"""Pruebas del registro de operaciones reales (Módulo 13).

La distinción que estas pruebas protegen: una operación REAL es dinero
ejecutado en una cuenta; una operación del simulador es una hipótesis. El
sistema nunca debe presentarlas como equivalentes.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from qq_core.domain.signal import Direction
from qq_core.execution.journal import (
    TradeJournal,
    account_summary,
    execution_quality,
    strategy_performance,
)
from qq_core.execution.trade import ExitReason, RealTrade, TradeStatus, build_trade_id


def _operacion(**overrides) -> RealTrade:
    base = dict(
        trade_id="US500:2026-07-01",
        symbol="US500",
        direction=Direction.LONG,
        units=Decimal("2"),
        entry_date=date(2026, 7, 1),
        entry_price=Decimal("5000"),
        stop_price=Decimal("4900"),
    )
    base.update(overrides)
    return RealTrade(**base)  # type: ignore[arg-type]


@pytest.fixture
def diario(tmp_path) -> TradeJournal:
    return TradeJournal(tmp_path / "diario.db")


# --------------------------------------------------------------------- #
# Modelo de operación real
# --------------------------------------------------------------------- #


def test_operacion_abierta_no_tiene_resultado_sin_precio() -> None:
    """Una posición abierta sin precio actual no inventa un resultado."""
    op = _operacion()
    assert op.status is TradeStatus.OPEN
    assert op.pnl() == Decimal(0)


def test_resultado_de_posicion_comprada() -> None:
    op = _operacion(exit_date=date(2026, 7, 10), exit_price=Decimal("5100"))
    assert op.pnl() == Decimal("200")  # (5100-5000) * 2


def test_resultado_de_posicion_vendida() -> None:
    """CA-52: en una venta se gana cuando el precio baja."""
    op = _operacion(
        direction=Direction.SHORT,
        stop_price=Decimal("5100"),
        exit_date=date(2026, 7, 10),
        exit_price=Decimal("4900"),
    )
    assert op.pnl() == Decimal("200")


def test_posicion_abierta_se_valora_a_precio_actual() -> None:
    op = _operacion()
    assert op.pnl(Decimal("5050")) == Decimal("100")


def test_direccion_flat_no_es_operacion_real() -> None:
    """No se puede ejecutar una orden 'sin posición'."""
    with pytest.raises(ValidationError):
        _operacion(direction=Direction.FLAT)


def test_cierre_incoherente_se_rechaza() -> None:
    """Una operación cerrada necesita fecha Y precio de salida."""
    with pytest.raises(ValidationError):
        _operacion(exit_date=date(2026, 7, 10))
    with pytest.raises(ValidationError):
        _operacion(exit_price=Decimal("5100"))


def test_cierre_anterior_a_apertura_se_rechaza() -> None:
    with pytest.raises(ValidationError):
        _operacion(exit_date=date(2026, 6, 1), exit_price=Decimal("5100"))


def test_unidades_deben_ser_positivas() -> None:
    """La dirección va en `direction`, no en el signo de las unidades.

    Permitir unidades negativas crearía dos formas de expresar una venta y
    duplicaría la lógica de cálculo de resultado.
    """
    with pytest.raises(ValidationError):
        _operacion(units=Decimal("-2"))


# --------------------------------------------------------------------- #
# Calidad de ejecución — el valor propio del módulo
# --------------------------------------------------------------------- #


def test_deslizamiento_positivo_significa_peor_ejecucion() -> None:
    """CA-53: entrar más caro en una compra es deslizamiento adverso.

    Es un coste real que ningún backtest recoge, porque en el backtest se
    entra siempre al precio teórico.
    """
    op = _operacion(entry_price=Decimal("5010"), signal_entry_price=Decimal("5000"))
    assert op.slippage == Decimal("10")
    assert op.slippage_pct == 0.2


def test_deslizamiento_en_venta_invierte_el_signo() -> None:
    """En una venta, entrar más BARATO es peor."""
    op = _operacion(
        direction=Direction.SHORT,
        stop_price=Decimal("5100"),
        entry_price=Decimal("4990"),
        signal_entry_price=Decimal("5000"),
    )
    assert op.slippage == Decimal("10")


def test_retraso_de_decision() -> None:
    """CA-54: días entre la señal y la ejecución real.

    Un retraso sistemático significa operar con información envejecida.
    """
    op = _operacion(signal_date=date(2026, 6, 30))
    assert op.decision_lag_days == 1


def test_operacion_sin_senal_se_marca_como_discrecional() -> None:
    """CA-55: distinguir el sistema del criterio del operador.

    Sin esta separación sería imposible saber si los resultados vienen de las
    estrategias o de las decisiones propias.
    """
    assert _operacion().followed_signal is False
    assert _operacion(signal_id="tf:US500:2026-06-30").followed_signal is True


def test_disciplina_con_el_stop() -> None:
    """Detecta si se dejó correr una pérdida más allá del nivel fijado."""
    respetada = _operacion(
        exit_date=date(2026, 7, 10), exit_price=Decimal("4950"),
        exit_reason=ExitReason.MANUAL,
    )
    assert respetada.respected_stop is True

    incumplida = _operacion(
        exit_date=date(2026, 7, 10), exit_price=Decimal("4800"),
        exit_reason=ExitReason.MANUAL,
    )
    assert incumplida.respected_stop is False


# --------------------------------------------------------------------- #
# Diario
# --------------------------------------------------------------------- #


def test_registrar_y_recuperar(diario: TradeJournal) -> None:
    diario.record(_operacion())
    recuperada = diario.get("US500:2026-07-01")
    assert recuperada is not None
    assert recuperada.symbol == "US500"
    assert recuperada.entry_price == Decimal("5000")


def test_precision_decimal_en_el_diario(diario: TradeJournal) -> None:
    """CA-56: los precios no pierden precisión al guardarse.

    En un registro contable, redondear al guardar produce descuadres que se
    acumulan y hacen imposible conciliar con el extracto del bróker.
    """
    diario.record(
        _operacion(
            symbol="EURUSD", entry_price=Decimal("1.08345"),
            stop_price=Decimal("1.07999"), units=Decimal("10000.5"),
        )
    )
    op = diario.get("US500:2026-07-01")
    assert op is not None
    assert str(op.entry_price) == "1.08345"
    assert str(op.units) == "10000.5"


def test_cerrar_operacion(diario: TradeJournal) -> None:
    diario.record(_operacion())
    assert len(diario.open_trades()) == 1

    cerrada = diario.close_trade(
        "US500:2026-07-01", date(2026, 7, 15), Decimal("5100"), ExitReason.TARGET
    )
    assert cerrada is not None
    assert cerrada.status is TradeStatus.CLOSED
    assert len(diario.open_trades()) == 0
    assert len(diario.closed_trades()) == 1


def test_correccion_conserva_el_valor_anterior(diario: TradeJournal) -> None:
    """CA-57: modificar una operación no borra lo que decía antes.

    En un registro contable, saber qué se cambió y cuándo forma parte del
    registro. Sobrescribir en silencio destruye la auditoría.
    """
    diario.record(_operacion())
    diario.record(_operacion(entry_price=Decimal("5005")))

    historial = diario._conn.execute(
        "SELECT COUNT(*) AS n FROM real_trade_history WHERE trade_id = ?",
        ("US500:2026-07-01",),
    ).fetchone()
    assert historial["n"] == 1
    assert diario.get("US500:2026-07-01").entry_price == Decimal("5005")


def test_borrado_deja_rastro(diario: TradeJournal) -> None:
    diario.record(_operacion())
    diario.delete("US500:2026-07-01")
    assert diario.get("US500:2026-07-01") is None
    fila = diario._conn.execute(
        "SELECT COUNT(*) AS n FROM real_trade_history"
    ).fetchone()
    assert fila["n"] == 1


def test_identificador_incluye_ticket_del_broker() -> None:
    """Permite conciliar el diario con el extracto de la cuenta."""
    con = build_trade_id("US500", date(2026, 7, 1), "98765")
    sin = build_trade_id("US500", date(2026, 7, 1))
    assert "98765" in con and con != sin


# --------------------------------------------------------------------- #
# Analítica de la cuenta
# --------------------------------------------------------------------- #


def test_resumen_separa_realizado_de_no_realizado(diario: TradeJournal) -> None:
    """CA-58: el resultado de posiciones abiertas no es dinero cobrado.

    Mezclarlos daría una imagen de la cuenta que no corresponde con el saldo
    disponible.
    """
    diario.record(
        _operacion(
            trade_id="cerrada", exit_date=date(2026, 7, 10),
            exit_price=Decimal("5100"),
        )
    )
    diario.record(_operacion(trade_id="abierta", symbol="XAUUSD"))

    r = account_summary(
        diario.all_trades(), Decimal("100000"), {"XAUUSD": Decimal("5050")}
    )
    assert r["resultado_realizado"] == 200.0
    assert r["resultado_no_realizado"] == 100.0
    assert r["capital_actual"] == 100300.0


def test_resumen_sin_operaciones() -> None:
    r = account_summary([], Decimal("100000"))
    assert r["capital_actual"] == 100000.0
    assert r["operaciones_totales"] == 0


def test_calidad_de_ejecucion_separa_señal_de_discrecional() -> None:
    """CA-59: se puede evaluar el sistema aparte del criterio del operador."""
    con_senal = _operacion(
        trade_id="a", signal_id="tf:US500:2026-06-30",
        signal_entry_price=Decimal("4990"), signal_date=date(2026, 6, 30),
        strategy="trend_following",
        exit_date=date(2026, 7, 10), exit_price=Decimal("5100"),
    )
    discrecional = _operacion(
        trade_id="b", exit_date=date(2026, 7, 5), exit_price=Decimal("4950")
    )

    q = execution_quality([con_senal, discrecional])
    assert q["operaciones_con_senal"] == 1
    assert q["operaciones_discrecionales"] == 1
    assert q["pct_con_senal"] == 50.0
    assert q["resultado_con_senal"] == 200.0
    assert q["resultado_discrecional"] == -100.0


def test_calidad_sin_operaciones() -> None:
    assert execution_quality([])["sin_datos"] is True


def test_resultado_por_estrategia() -> None:
    """Permite ver qué estrategias funcionan con dinero real."""
    ops = [
        _operacion(
            trade_id="a", strategy="trend_following",
            exit_date=date(2026, 7, 10), exit_price=Decimal("5100"),
        ),
        _operacion(
            trade_id="b", strategy="trend_following",
            exit_date=date(2026, 7, 12), exit_price=Decimal("4950"),
        ),
        _operacion(
            trade_id="c", exit_date=date(2026, 7, 14), exit_price=Decimal("5050")
        ),
    ]
    filas = strategy_performance(ops)
    assert len(filas) == 2
    tf = next(f for f in filas if f["Estrategia"] == "trend_following")
    assert tf["Operaciones"] == 2
    assert tf["Aciertos %"] == 50.0


def test_operaciones_abiertas_excluidas_del_resultado_por_estrategia() -> None:
    """Una posición abierta aún no tiene resultado que atribuir."""
    assert strategy_performance([_operacion(strategy="trend_following")]) == []


# --------------------------------------------------------------------- #
# Seguimiento de señales en el tiempo
# --------------------------------------------------------------------- #


def _senal_base(fuerza=None, direccion=None, dias_atras=5):
    from datetime import timedelta

    from qq_core.domain.signal import Horizon, Signal, SignalStrength

    ahora = datetime.now(timezone.utc) - timedelta(days=dias_atras)
    return Signal(
        signal_id="tf:US500", strategy="trend_following",
        strategy_label="Seguimiento de tendencia", strategy_version="2.0.0",
        symbol="US500", as_of=ahora, generated_at=ahora,
        direction=direccion or Direction.LONG,
        strength=fuerza or SignalStrength.STRONG,
        horizon=Horizon.MONTH_1,
        observed_price=Decimal("5000"), entry_price=Decimal("5000"),
        target_price=Decimal("5300"), stop_price=Decimal("4900"),
        rationale="prueba",
    )


def test_senal_que_se_debilita_se_detecta() -> None:
    """CA-71: el operador sabe si su entrada fuerte sigue siéndolo.

    Es lo que responde a «entré con señal fuerte a tres meses, ¿sigue
    siendo válida dos semanas después?».
    """
    from qq_core.domain.signal import SignalStrength
    from qq_core.signals.tracking import SignalHealth, track_signal

    original = _senal_base(fuerza=SignalStrength.STRONG)
    hoy = _senal_base(fuerza=SignalStrength.WEAK, dias_atras=0)

    estado = track_signal(original, hoy, Decimal("5050"))
    assert estado.health is SignalHealth.WEAKENING
    assert "debilita" in estado.message


def test_senal_invertida_requiere_atencion() -> None:
    """Si la estrategia ahora dice lo contrario, hay que avisar."""
    from qq_core.signals.tracking import SignalHealth, track_signal

    original = _senal_base()
    hoy = _senal_base(direccion=Direction.SHORT, dias_atras=0)

    estado = track_signal(original, hoy, Decimal("4950"))
    assert estado.health is SignalHealth.REVERSED
    assert estado.needs_attention


def test_señal_cerrada_cuando_ya_no_hay_recomendacion() -> None:
    from qq_core.signals.tracking import SignalHealth, track_signal

    estado = track_signal(_senal_base(), None, Decimal("5100"))
    assert estado.health is SignalHealth.CLOSED
    assert estado.needs_attention


def test_horizonte_agotado_se_avisa() -> None:
    """Superado el tiempo previsto, conviene revisar si compensa mantener."""
    from qq_core.signals.tracking import SignalHealth, track_signal

    original = _senal_base(dias_atras=60)  # horizonte de 22 sesiones
    hoy = _senal_base(dias_atras=0)
    estado = track_signal(original, hoy, Decimal("5100"))
    assert estado.health is SignalHealth.EXPIRED


def test_avance_al_objetivo_se_calcula() -> None:
    """El operador ve cuánto le falta para el objetivo."""
    from qq_core.signals.tracking import track_signal

    # Entrada 5000, objetivo 5300: a 5150 se ha recorrido la mitad.
    estado = track_signal(_senal_base(), _senal_base(dias_atras=0), Decimal("5150"))
    assert 45 < (estado.progress_to_target_pct or 0) < 55


def test_seguimiento_ordena_por_urgencia() -> None:
    """Lo que requiere atención aparece primero."""
    from qq_core.domain.signal import SignalStrength
    from qq_core.signals.tracking import SignalHealth, track_many

    tranquila = _senal_base()
    invertida = _senal_base()
    object.__setattr__(invertida, "symbol", "XAUUSD")

    actuales = {
        ("US500", "trend_following"): _senal_base(dias_atras=0),
        ("XAUUSD", "trend_following"): _senal_base(
            direccion=Direction.SHORT, dias_atras=0
        ),
    }
    estados = track_many(
        [tranquila, invertida], actuales,
        {"US500": Decimal("5050"), "XAUUSD": Decimal("5050")},
    )
    assert estados[0].health is SignalHealth.REVERSED
