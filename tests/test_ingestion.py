"""Pruebas del servicio de ingesta, el repositorio y los adaptadores.

El test central es `test_reingestion_is_byte_identical`: es el criterio de
aceptación principal del Módulo 01.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qq_core.adapters.stooq import StooqProvider
from qq_core.adapters.mt5_gateway import MT5GatewayProvider
from qq_core.domain.bar import Bar
from qq_core.domain.enums import DataSource, Timeframe
from qq_core.ingestion.service import IngestionRequest, IngestionService, utc
from qq_core.ports.data_provider import (
    FetchResult,
    ProviderCapabilities,
    ProviderContractError,
    ProviderError,
)
from qq_core.domain.provenance import Provenance, hash_payload
from qq_core.storage.repository import InMemoryBarRepository

CSV_OK = """Date,Open,High,Low,Close,Volume
2025-01-02,1.0350,1.0400,1.0300,1.0380,125000
2025-01-03,1.0380,1.0420,1.0360,1.0410,131000
2025-01-06,1.0410,1.0450,1.0390,1.0400,118000
"""

CSV_REVISED = """Date,Open,High,Low,Close,Volume
2025-01-02,1.0350,1.0400,1.0300,1.0380,125000
2025-01-03,1.0380,1.0420,1.0360,1.0415,131000
2025-01-06,1.0410,1.0450,1.0390,1.0400,118000
"""


@pytest.fixture
def repo() -> InMemoryBarRepository:
    return InMemoryBarRepository()


def stooq_with(payload: str) -> StooqProvider:
    return StooqProvider(fetcher=lambda url: payload)


def request_for(**overrides) -> IngestionRequest:
    base = dict(
        provider_symbol="eurusd",
        canonical_symbol="EURUSD",
        timeframe=Timeframe.D1,
        start=utc(2025, 1, 1),
        end=utc(2025, 1, 10),
        resume=False,
    )
    base.update(overrides)
    return IngestionRequest(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------- #
# Adaptador Stooq
# --------------------------------------------------------------------- #


def test_stooq_parses_and_filters_range() -> None:
    provider = stooq_with(CSV_OK)
    result = provider.fetch_bars(
        "eurusd", "EURUSD", Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 6)
    )
    # 2025-01-06 queda fuera: el rango es semiabierto.
    assert len(result.bars) == 2
    assert result.bars[0].close == Decimal("1.0380")
    assert result.provenance.source is DataSource.STOOQ


def test_stooq_rejects_unexpected_header() -> None:
    """CA-08: una respuesta con estructura distinta no se parsea a ciegas."""
    provider = stooq_with("Fecha,Apertura\n2025-01-02,1.03\n")
    with pytest.raises(ProviderContractError):
        provider.fetch_bars(
            "eurusd", "EURUSD", Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 10)
        )


def test_stooq_rejects_incoherent_row() -> None:
    """Una fila con high < low es error de proveedor, no dato a limpiar."""
    bad = "Date,Open,High,Low,Close,Volume\n2025-01-02,1.03,1.01,1.05,1.04,1000\n"
    with pytest.raises(ProviderContractError):
        stooq_with(bad).fetch_bars(
            "eurusd", "EURUSD", Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 10)
        )


def test_stooq_empty_response_yields_no_bars() -> None:
    assert (
        stooq_with("").fetch_bars(
            "eurusd", "EURUSD", Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 10)
        ).bars
        == ()
    )


def test_stooq_rejects_unsupported_timeframe() -> None:
    with pytest.raises(ProviderError):
        stooq_with(CSV_OK).fetch_bars(
            "eurusd", "EURUSD", Timeframe.M5, utc(2025, 1, 1), utc(2025, 1, 10)
        )


# --------------------------------------------------------------------- #
# Adaptador MT5 gateway
# --------------------------------------------------------------------- #


def test_mt5_discards_tick_volume() -> None:
    """CA-09: el tick volume de MT5 no se almacena como volumen real."""
    payload = (
        '[{"time": 1735776000, "open": 1.035, "high": 1.04, '
        '"low": 1.03, "close": 1.038, "tick_volume": 45231}]'
    )
    provider = MT5GatewayProvider(transport=lambda url, params: payload)
    result = provider.fetch_bars(
        "EURUSD.gbi", "EURUSD", Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 10)
    )
    assert len(result.bars) == 1
    assert result.bars[0].volume is None


def test_mt5_gateway_unreachable_is_retryable_error() -> None:
    def boom(url, params):
        raise ConnectionRefusedError("gateway caído")

    provider = MT5GatewayProvider(transport=boom)
    with pytest.raises(ProviderError):
        provider.fetch_bars(
            "EURUSD.gbi", "EURUSD", Timeframe.D1, utc(2025, 1, 1), utc(2025, 1, 10)
        )


def test_core_never_imports_metatrader5() -> None:
    """CA-10: garantía estructural de portabilidad.

    Si este test falla, el núcleo ha quedado atado a Windows.
    """
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "qq_core"
    offenders = [
        p.name
        for p in root.rglob("*.py")
        if "import MetaTrader5" in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"El núcleo importa MetaTrader5 en: {offenders}"


def test_no_order_sending_code_in_core() -> None:
    """CA-11: no existe código capaz de enviar órdenes."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "src" / "qq_core"
    forbidden = ("order_send", "OrderSend", "positions_close")
    offenders = [
        (p.name, token)
        for p in root.rglob("*.py")
        for token in forbidden
        if token in p.read_text(encoding="utf-8")
    ]
    assert not offenders, f"Código de ejecución detectado: {offenders}"


# --------------------------------------------------------------------- #
# Repositorio: idempotencia
# --------------------------------------------------------------------- #


def test_reingestion_is_byte_identical(repo: InMemoryBarRepository) -> None:
    """CA-12 (CRITERIO DE ACEPTACIÓN PRINCIPAL DEL MÓDULO 01).

    Reingestar el mismo rango dos veces deja el almacén exactamente igual.
    Sin esto, ningún backtest es reproducible.
    """
    service = IngestionService(stooq_with(CSV_OK), repo, sleeper=lambda s: None)
    req = request_for()

    first = service.ingest(req)
    snapshot = {b.natural_key: b.content_hash for b in _all_bars(repo)}

    second = service.ingest(req)
    after = {b.natural_key: b.content_hash for b in _all_bars(repo)}

    assert first.report.inserted == 3
    assert second.report.inserted == 0
    assert second.report.unchanged == 3
    assert second.report.revised == 0
    assert snapshot == after
    assert repo.revision_log == []


def test_provider_revision_is_detected_and_logged(repo: InMemoryBarRepository) -> None:
    """CA-13: si el proveedor reescribe el histórico, queda registrado."""
    req = request_for()
    IngestionService(stooq_with(CSV_OK), repo, sleeper=lambda s: None).ingest(req)
    result = IngestionService(
        stooq_with(CSV_REVISED), repo, sleeper=lambda s: None
    ).ingest(req)

    assert result.report.revised == 1
    assert result.report.unchanged == 2
    assert len(repo.revision_log) == 1


def test_first_ingestion_time_preserved_on_noop(repo: InMemoryBarRepository) -> None:
    """Un no-op no debe pisar la procedencia original."""
    req = request_for()
    service = IngestionService(stooq_with(CSV_OK), repo, sleeper=lambda s: None)
    service.ingest(req)
    bar = _all_bars(repo)[0]
    first_prov = repo.get_provenance(bar)
    assert first_prov is not None
    service.ingest(req)
    assert repo.get_provenance(bar) is first_prov


# --------------------------------------------------------------------- #
# Servicio de ingesta: troceado, reanudación, reintentos
# --------------------------------------------------------------------- #


class RecordingProvider:
    """Proveedor sintético que registra los rangos que se le piden."""

    def __init__(self, max_bars: int = 2, fail_times: int = 0) -> None:
        self.calls: list[tuple[datetime, datetime]] = []
        self._fail_times = fail_times
        self._caps = ProviderCapabilities(
            source=DataSource.STOOQ,
            timeframes=frozenset({Timeframe.D1}),
            asset_classes=frozenset(),
            max_bars_per_request=max_bars,
        )

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._caps

    def fetch_bars(
        self, provider_symbol, canonical_symbol, timeframe, start, end
    ) -> FetchResult:
        self.calls.append((start, end))
        if self._fail_times > 0:
            self._fail_times -= 1
            raise ProviderError("fallo transitorio")
        bars = []
        cursor = start
        while cursor < end:
            bars.append(
                Bar(
                    symbol=canonical_symbol,
                    timeframe=timeframe,
                    ts=cursor,
                    source=DataSource.STOOQ,
                    open=Decimal("1"),
                    high=Decimal("2"),
                    low=Decimal("0.5"),
                    close=Decimal("1.5"),
                )
            )
            cursor += timedelta(seconds=timeframe.seconds)
        return FetchResult(
            bars=tuple(bars),
            provenance=Provenance(
                source=DataSource.STOOQ,
                provider_symbol=provider_symbol,
                ingested_at=datetime.now(timezone.utc),
                raw_payload_hash=hash_payload(f"{start}{end}"),
            ),
        )


def test_range_is_chunked_by_provider_limit(repo: InMemoryBarRepository) -> None:
    """CA-14: el rango se trocea según max_bars_per_request, sin solapes."""
    provider = RecordingProvider(max_bars=2)
    service = IngestionService(provider, repo, sleeper=lambda s: None)
    service.ingest(request_for(start=utc(2025, 1, 1), end=utc(2025, 1, 7)))

    assert len(provider.calls) == 3
    for (_, prev_end), (next_start, _) in zip(provider.calls, provider.calls[1:]):
        assert prev_end == next_start  # contiguos y semiabiertos


def test_resume_starts_from_watermark(repo: InMemoryBarRepository) -> None:
    """CA-15: tras una interrupción, no se reingesta el histórico completo."""
    provider = RecordingProvider(max_bars=100)
    service = IngestionService(provider, repo, sleeper=lambda s: None)
    service.ingest(request_for(start=utc(2025, 1, 1), end=utc(2025, 1, 5), resume=True))
    provider.calls.clear()

    service.ingest(request_for(start=utc(2025, 1, 1), end=utc(2025, 1, 8), resume=True))
    assert provider.calls[0][0] == utc(2025, 1, 5)


def test_transient_error_is_retried(repo: InMemoryBarRepository) -> None:
    provider = RecordingProvider(max_bars=100, fail_times=2)
    service = IngestionService(provider, repo, max_retries=3, sleeper=lambda s: None)
    result = service.ingest(request_for(start=utc(2025, 1, 1), end=utc(2025, 1, 4)))
    assert result.is_complete
    assert len(provider.calls) == 3


def test_contract_error_is_not_retried(repo: InMemoryBarRepository) -> None:
    """CA-16: una respuesta malformada no consume cuota en reintentos."""
    calls = {"n": 0}

    def bad_fetch(url: str) -> str:
        calls["n"] += 1
        return "Fecha,Apertura\n2025-01-02,1.03\n"

    service = IngestionService(
        StooqProvider(fetcher=bad_fetch), repo, max_retries=3, sleeper=lambda s: None
    )
    result = service.ingest(request_for())
    assert calls["n"] == 1
    assert not result.is_complete


def test_partial_failure_does_not_abort_remaining_chunks(
    repo: InMemoryBarRepository,
) -> None:
    """Un chunk fallido se reporta; los demás se ingestan igualmente."""
    provider = RecordingProvider(max_bars=2, fail_times=3)
    service = IngestionService(provider, repo, max_retries=3, sleeper=lambda s: None)
    result = service.ingest(request_for(start=utc(2025, 1, 1), end=utc(2025, 1, 7)))
    assert len(result.failed_chunks) == 1
    assert result.report.inserted > 0
    assert not result.is_complete


def test_invalid_range_fails_fast(repo: InMemoryBarRepository) -> None:
    service = IngestionService(RecordingProvider(), repo, sleeper=lambda s: None)
    with pytest.raises(ValueError):
        service.ingest(request_for(start=utc(2025, 1, 10), end=utc(2025, 1, 1)))


def test_naive_datetimes_rejected(repo: InMemoryBarRepository) -> None:
    service = IngestionService(RecordingProvider(), repo, sleeper=lambda s: None)
    with pytest.raises(ValueError):
        service.ingest(
            request_for(start=datetime(2025, 1, 1), end=datetime(2025, 1, 5))
        )


def test_unsupported_timeframe_fails_before_network(
    repo: InMemoryBarRepository,
) -> None:
    provider = RecordingProvider()
    service = IngestionService(provider, repo, sleeper=lambda s: None)
    with pytest.raises(ValueError):
        service.ingest(request_for(timeframe=Timeframe.M1))
    assert provider.calls == []


def test_two_sources_coexist_for_same_symbol(repo: InMemoryBarRepository) -> None:
    """CA-17: GBI y Stooq para EURUSD conviven; el Módulo 02 los reconcilia."""
    IngestionService(stooq_with(CSV_OK), repo, sleeper=lambda s: None).ingest(
        request_for()
    )
    mt5_payload = (
        '[{"time": 1735776000, "open": 1.0351, "high": 1.0401, '
        '"low": 1.0301, "close": 1.0381, "tick_volume": 1}]'
    )
    IngestionService(
        MT5GatewayProvider(transport=lambda url, params: mt5_payload),
        repo,
        sleeper=lambda s: None,
    ).ingest(request_for(provider_symbol="EURUSD.gbi"))

    stooq_bars = repo.get_bars(
        "EURUSD", Timeframe.D1, DataSource.STOOQ, utc(2025, 1, 1), utc(2025, 1, 10)
    )
    mt5_bars = repo.get_bars(
        "EURUSD", Timeframe.D1, DataSource.MT5_GBI, utc(2025, 1, 1), utc(2025, 1, 10)
    )
    assert len(stooq_bars) == 3
    assert len(mt5_bars) == 1
    assert stooq_bars[0].close != mt5_bars[0].close


def _all_bars(repo: InMemoryBarRepository) -> list[Bar]:
    return repo.get_bars(
        "EURUSD", Timeframe.D1, DataSource.STOOQ, utc(2020, 1, 1), utc(2030, 1, 1)
    )
