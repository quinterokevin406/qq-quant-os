"""Puerto de persistencia e implementación en memoria.

La implementación en memoria no es un juguete: es la que permite testear el
servicio de ingesta sin levantar TimescaleDB, y sirve de especificación
ejecutable del comportamiento que el adaptador de Postgres debe replicar.

El adaptador real de TimescaleDB implementa el mismo `Protocol` y debe pasar
exactamente la misma batería de tests (ver `tests/test_repository_contract.py`
en el plan del Módulo 01; aquí se incluye la suite genérica).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Protocol, runtime_checkable

from qq_core.domain.bar import Bar
from qq_core.domain.enums import DataSource, Timeframe
from qq_core.domain.provenance import Provenance


@dataclass(frozen=True)
class UpsertReport:
    """Resultado de una escritura idempotente.

    Attributes:
        inserted: Barras nuevas.
        unchanged: Barras ya presentes con contenido idéntico (no-op).
        revised: Barras ya presentes cuyo contenido CAMBIÓ. Este número debe
            vigilarse: un valor alto significa que el proveedor está reescribiendo
            el histórico, lo que invalida backtests previos.
    """

    inserted: int = 0
    unchanged: int = 0
    revised: int = 0

    @property
    def total(self) -> int:
        return self.inserted + self.unchanged + self.revised

    def merged_with(self, other: "UpsertReport") -> "UpsertReport":
        return UpsertReport(
            inserted=self.inserted + other.inserted,
            unchanged=self.unchanged + other.unchanged,
            revised=self.revised + other.revised,
        )


@runtime_checkable
class BarRepository(Protocol):
    """Persistencia de barras con semántica idempotente."""

    def upsert_bars(
        self, bars: Iterable[Bar], provenance: Provenance
    ) -> UpsertReport: ...

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        source: DataSource,
        start: datetime,
        end: datetime,
    ) -> list[Bar]: ...

    def get_watermark(
        self, symbol: str, timeframe: Timeframe, source: DataSource
    ) -> datetime | None:
        """Último `ts` ingestado. `None` si no hay datos.

        Es lo que hace la ingesta reanudable: tras una caída, se retoma desde
        aquí en lugar de reingestar todo el histórico.
        """
        ...


class InMemoryBarRepository:
    """Implementación en memoria. Especificación ejecutable del contrato."""

    def __init__(self) -> None:
        self._bars: dict[tuple[str, str, datetime, str], Bar] = {}
        self._hashes: dict[tuple[str, str, datetime, str], str] = {}
        self._provenance: dict[tuple[str, str, datetime, str], Provenance] = {}
        self.revision_log: list[tuple[tuple[str, str, datetime, str], str, str]] = []

    def upsert_bars(self, bars: Iterable[Bar], provenance: Provenance) -> UpsertReport:
        """Escribe barras. Reescribir el mismo dato no cambia nada.

        Args:
            bars: Barras a escribir.
            provenance: Procedencia común del lote.

        Returns:
            Conteo de insertadas, sin cambios y revisadas.
        """
        report = UpsertReport()
        for bar in bars:
            key = bar.natural_key
            new_hash = bar.content_hash
            existing_hash = self._hashes.get(key)
            if existing_hash is None:
                report = report.merged_with(UpsertReport(inserted=1))
            elif existing_hash == new_hash:
                # No-op deliberado: no se toca la procedencia almacenada para
                # que `ingested_at` siga reflejando la PRIMERA vez que se vio
                # este dato exacto.
                report = report.merged_with(UpsertReport(unchanged=1))
                continue
            else:
                self.revision_log.append((key, existing_hash, new_hash))
                report = report.merged_with(UpsertReport(revised=1))
            self._bars[key] = bar
            self._hashes[key] = new_hash
            self._provenance[key] = provenance
        return report

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        source: DataSource,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Devuelve barras del rango [start, end) ordenadas por `ts`."""
        return sorted(
            (
                b
                for b in self._bars.values()
                if b.symbol == symbol
                and b.timeframe is timeframe
                and b.source is source
                and start <= b.ts < end
            ),
            key=lambda b: b.ts,
        )

    def get_watermark(
        self, symbol: str, timeframe: Timeframe, source: DataSource
    ) -> datetime | None:
        """Último `ts` conocido para la serie, o `None` si está vacía."""
        stamps = [
            b.ts
            for b in self._bars.values()
            if b.symbol == symbol and b.timeframe is timeframe and b.source is source
        ]
        return max(stamps) if stamps else None

    def get_provenance(self, bar: Bar) -> Provenance | None:
        """Procedencia registrada para una barra concreta."""
        return self._provenance.get(bar.natural_key)
