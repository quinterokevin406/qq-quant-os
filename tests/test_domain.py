"""Pruebas del modelo de dominio.

Cada test corresponde a un criterio de aceptación o a un caso límite
documentado en docs/modules/01-data-engine/DESIGN.md.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from pydantic import TypeAdapter, ValidationError

from qq_core.domain.bar import Bar
from qq_core.domain.enums import AssetClass, DataSource, RollPolicy, Timeframe
from qq_core.domain.instrument import CFD, Equity, Future, FxSpot, Instrument
from qq_core.domain.provenance import Provenance, content_hash, hash_payload

INSTRUMENT_ADAPTER = TypeAdapter(Instrument)


# --------------------------------------------------------------------- #
# Instrument: la unión discriminada hace imposible el estado inválido
# --------------------------------------------------------------------- #


def test_future_requires_expiry() -> None:
    """CA-01: no se puede construir un futuro sin vencimiento."""
    with pytest.raises(ValidationError):
        Future(
            symbol="ESZ25",
            name="E-mini S&P 500 Dec 2025",
            currency="USD",
            tick_size=Decimal("0.25"),
            root="ES",
            contract_multiplier=Decimal("50"),
            exchange="CME",
        )


def test_future_valid() -> None:
    fut = Future(
        symbol="ESZ25",
        name="E-mini S&P 500 Dec 2025",
        currency="USD",
        tick_size=Decimal("0.25"),
        root="ES",
        expiry=date(2025, 12, 19),
        contract_multiplier=Decimal("50"),
        exchange="CME",
    )
    assert fut.asset_class is AssetClass.FUTURE
    assert fut.roll_policy is RollPolicy.PANAMA


def test_cfd_is_not_a_future() -> None:
    """CA-02: un CFD sobre índice NO es tipable como futuro.

    Es la salvaguarda contra backtestear carry sobre el spread del bróker.
    """
    cfd = CFD(
        symbol="US500",
        name="US 500 CFD (GBI)",
        currency="USD",
        tick_size=Decimal("0.1"),
        issuer="GBI",
        underlying_ref="S&P 500",
        contract_multiplier=Decimal("1"),
    )
    assert not isinstance(cfd, Future)
    # Sin términos de financiación auditados, `financing_applies` es False:
    # el sistema no asume un coste que no ha medido. El catálogo real sí los
    # lleva (ver test_financing_applies_refleja_presencia_de_terminos).
    assert cfd.financing_applies is False


def test_instrument_union_dispatches_on_asset_class() -> None:
    """CA-03: la unión deserializa a la subclase correcta."""
    payload = {
        "asset_class": "fx_spot",
        "symbol": "EURUSD",
        "name": "Euro / US Dollar",
        "currency": "USD",
        "tick_size": "0.00001",
        "base": "EUR",
        "quote": "USD",
        "pip_size": "0.0001",
    }
    inst = INSTRUMENT_ADAPTER.validate_python(payload)
    assert isinstance(inst, FxSpot)
    assert inst.pip_size == Decimal("0.0001")


def test_symbol_must_be_canonical() -> None:
    with pytest.raises(ValidationError):
        Equity(
            symbol="aapl",
            name="Apple Inc",
            currency="USD",
            tick_size=Decimal("0.01"),
            exchange="NASDAQ",
        )


def test_instrument_is_immutable() -> None:
    """Un instrumento no cambia; se versiona. Mutarlo corrompe el histórico."""
    eq = Equity(
        symbol="AAPL",
        name="Apple Inc",
        currency="USD",
        tick_size=Decimal("0.01"),
        exchange="NASDAQ",
    )
    with pytest.raises(ValidationError):
        eq.tick_size = Decimal("0.05")  # type: ignore[misc]


def test_unknown_field_rejected() -> None:
    """`extra='forbid'`: un campo mal escrito falla en lugar de ignorarse."""
    with pytest.raises(ValidationError):
        Equity(
            symbol="AAPL",
            name="Apple Inc",
            currency="USD",
            tick_size=Decimal("0.01"),
            exchange="NASDAQ",
            tik_size=Decimal("0.01"),  # typo deliberado
        )


# --------------------------------------------------------------------- #
# Bar: invariantes estructurales
# --------------------------------------------------------------------- #


def _bar(**overrides) -> Bar:
    base = dict(
        symbol="EURUSD",
        timeframe=Timeframe.D1,
        ts=datetime(2025, 1, 2, tzinfo=timezone.utc),
        source=DataSource.STOOQ,
        open=Decimal("1.0350"),
        high=Decimal("1.0400"),
        low=Decimal("1.0300"),
        close=Decimal("1.0380"),
        volume=Decimal("125000"),
    )
    base.update(overrides)
    return Bar(**base)  # type: ignore[arg-type]


def test_bar_rejects_naive_timestamp() -> None:
    """CA-04: nunca entra un timestamp sin zona horaria."""
    with pytest.raises(ValidationError):
        _bar(ts=datetime(2025, 1, 2))


def test_bar_normalises_to_utc() -> None:
    from datetime import timedelta, timezone as tz

    madrid = tz(timedelta(hours=1))
    bar = _bar(ts=datetime(2025, 1, 2, 1, 0, tzinfo=madrid))
    assert bar.ts == datetime(2025, 1, 2, 0, 0, tzinfo=timezone.utc)
    assert bar.ts.tzinfo is timezone.utc


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": Decimal("1.0200")},  # high < low
        {"low": Decimal("1.0390")},  # low > close
        {"high": Decimal("1.0360")},  # high < close
    ],
)
def test_bar_rejects_incoherent_ohlc(overrides: dict) -> None:
    """CA-05: una barra estructuralmente imposible no llega a la BD."""
    with pytest.raises(ValidationError):
        _bar(**overrides)


def test_bar_rejects_misaligned_timestamp() -> None:
    """CA-06: una barra H1 con ts en el minuto 37 es un bug del adaptador."""
    with pytest.raises(ValidationError):
        _bar(timeframe=Timeframe.H1, ts=datetime(2025, 1, 2, 10, 37, tzinfo=timezone.utc))


def test_bar_negative_volume_rejected() -> None:
    with pytest.raises(ValidationError):
        _bar(volume=Decimal("-1"))


def test_volume_none_is_distinct_from_zero() -> None:
    """Caso límite: ausencia de dato != volumen cero."""
    assert _bar(volume=None).volume is None
    assert _bar(volume=Decimal("0")).volume == Decimal("0")
    assert _bar(volume=None).content_hash != _bar(volume=Decimal("0")).content_hash


def test_content_hash_is_deterministic() -> None:
    """CA-07: el mismo contenido produce siempre el mismo hash."""
    assert _bar().content_hash == _bar().content_hash


def test_content_hash_changes_on_revision() -> None:
    assert _bar().content_hash != _bar(close=Decimal("1.0381")).content_hash


def test_natural_key_includes_source() -> None:
    """La misma barra de dos proveedores son datos distintos y coexisten."""
    a = _bar(source=DataSource.STOOQ)
    b = _bar(source=DataSource.MT5_GBI)
    assert a.natural_key != b.natural_key


# --------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------- #


def test_provenance_requires_utc() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            source=DataSource.STOOQ,
            provider_symbol="eurusd",
            ingested_at=datetime(2025, 1, 2),
            raw_payload_hash="a" * 64,
        )


def test_provenance_rejects_malformed_hash() -> None:
    with pytest.raises(ValidationError):
        Provenance(
            source=DataSource.STOOQ,
            provider_symbol="eurusd",
            ingested_at=datetime.now(timezone.utc),
            raw_payload_hash="not-a-hash",
        )


def test_hash_payload_stable_across_types() -> None:
    assert hash_payload("abc") == hash_payload(b"abc")


def test_content_hash_ignores_key_order() -> None:
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
