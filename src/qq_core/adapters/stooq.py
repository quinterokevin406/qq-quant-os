"""Adaptador Stooq.

Stooq entrega CSV diario sin autenticación. Es el proveedor con mejor relación
cobertura/fricción para la primera etapa, pero tiene limitaciones que se
declaran en `capabilities` en lugar de esconderse:

  - Sin intradía fiable en el endpoint público.
  - Sin point-in-time: el histórico se revisa.
  - Ajuste por corporate actions inconsistente según el mercado.

Se implementa contra un `fetcher` inyectable en lugar de llamar a `requests`
directamente. Motivo: hace el adaptador testeable sin red, y hace explícito
que la política de reintentos y timeouts es una decisión del orquestador, no
del adaptador.
"""

from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from pydantic import ValidationError

from qq_core.domain.bar import Bar
from qq_core.domain.enums import AssetClass, DataSource, Timeframe
from qq_core.domain.provenance import Provenance, hash_payload
from qq_core.ports.data_provider import (
    FetchResult,
    ProviderCapabilities,
    ProviderContractError,
    ProviderError,
)

STOOQ_CAPABILITIES = ProviderCapabilities(
    source=DataSource.STOOQ,
    timeframes=frozenset({Timeframe.D1, Timeframe.W1}),
    asset_classes=frozenset(
        {
            AssetClass.EQUITY,
            AssetClass.ETF,
            AssetClass.INDEX,
            AssetClass.FX_SPOT,
            AssetClass.FUTURE,
        }
    ),
    max_bars_per_request=100_000,
    rate_limit_per_minute=None,
    has_point_in_time=False,
    adjusts_corporate_actions=False,
    is_broker_specific=False,
    supports_volume=True,
    notes=(
        "El histórico se revisa sin aviso; vigilar bar_revision.",
        "Ajuste por splits inconsistente fuera de mercados US/PL.",
        "Sin intradía fiable en el endpoint público.",
    ),
)

_EXPECTED_HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


class StooqProvider:
    """Adaptador de solo lectura para Stooq.

    Args:
        fetcher: Función que recibe una URL y devuelve el cuerpo de la
            respuesta como texto. Inyectada para permitir tests sin red y
            para que la política de retry/timeout viva fuera del adaptador.
    """

    BASE_URL = "https://stooq.com/q/d/l/"

    def __init__(self, fetcher: Callable[[str], str]) -> None:
        self._fetch = fetcher

    @property
    def capabilities(self) -> ProviderCapabilities:
        return STOOQ_CAPABILITIES

    def fetch_bars(
        self,
        provider_symbol: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> FetchResult:
        """Descarga y parsea barras de Stooq en el rango [start, end).

        Args:
            provider_symbol: Símbolo Stooq. Ej. `eurusd`, `aapl.us`.
            canonical_symbol: Símbolo canónico interno a estampar.
            timeframe: Solo `D1` y `W1` están soportados.
            start: Inicio UTC inclusivo.
            end: Fin UTC exclusivo.

        Returns:
            Barras ordenadas por `ts` con su procedencia.

        Raises:
            ProviderError: El timeframe no está soportado o la descarga falló.
            ProviderContractError: El CSV no tiene la estructura esperada.
        """
        if timeframe not in self.capabilities.timeframes:
            raise ProviderError(
                f"Stooq no soporta {timeframe.value}; "
                f"soportados: {sorted(t.value for t in self.capabilities.timeframes)}"
            )

        params = {
            "s": provider_symbol,
            "i": "d" if timeframe is Timeframe.D1 else "w",
            "d1": start.strftime("%Y%m%d"),
            "d2": end.strftime("%Y%m%d"),
        }
        url = self.BASE_URL + "?" + "&".join(f"{k}={v}" for k, v in params.items())

        try:
            payload = self._fetch(url)
        except Exception as exc:  # noqa: BLE001 - se reetiqueta como error de proveedor
            raise ProviderError(f"Fallo al descargar de Stooq: {exc}") from exc

        bars = self._parse_csv(payload, canonical_symbol, timeframe, start, end)
        provenance = Provenance(
            source=DataSource.STOOQ,
            provider_symbol=provider_symbol,
            ingested_at=datetime.now(timezone.utc),
            raw_payload_hash=hash_payload(payload),
            request_params=params,
        )
        return FetchResult(bars=tuple(bars), provenance=provenance)

    def _parse_csv(
        self,
        payload: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Convierte el CSV de Stooq en barras canónicas.

        El filtrado por rango se repite aquí aunque la URL ya lo pida: nunca se
        confía en que el proveedor respete los parámetros de la petición.
        """
        reader = csv.reader(io.StringIO(payload.strip()))
        try:
            header = next(reader)
        except StopIteration:
            return []

        if header[: len(_EXPECTED_HEADER)] != _EXPECTED_HEADER:
            raise ProviderContractError(
                f"Cabecera inesperada de Stooq: {header}. "
                f"Esperada: {_EXPECTED_HEADER}"
            )

        bars: list[Bar] = []
        for line_no, row in enumerate(reader, start=2):
            if not row or all(not cell.strip() for cell in row):
                continue
            try:
                ts = datetime.strptime(row[0], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                volume = self._to_decimal(row[5]) if len(row) > 5 else None
                bar = Bar(
                    symbol=canonical_symbol,
                    timeframe=timeframe,
                    ts=ts,
                    source=DataSource.STOOQ,
                    open=self._to_decimal(row[1]),
                    high=self._to_decimal(row[2]),
                    low=self._to_decimal(row[3]),
                    close=self._to_decimal(row[4]),
                    volume=volume,
                )
            except (ValueError, IndexError, InvalidOperation, ValidationError) as exc:
                raise ProviderContractError(
                    f"Fila inválida en línea {line_no} de Stooq "
                    f"para {provider_symbol_repr(canonical_symbol)}: {row} ({exc})"
                ) from exc
            if start <= bar.ts < end:
                bars.append(bar)

        bars.sort(key=lambda b: b.ts)
        return bars

    @staticmethod
    def _to_decimal(raw: str) -> Decimal | None:
        """Convierte texto a `Decimal`, tratando vacío y `N/A` como ausente."""
        cleaned = raw.strip()
        if cleaned in {"", "N/A", "null"}:
            return None
        return Decimal(cleaned)


def provider_symbol_repr(symbol: str) -> str:
    """Representación segura de un símbolo para mensajes de error."""
    return symbol[:32]
