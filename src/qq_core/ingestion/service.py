"""Servicio de ingesta: orquesta proveedores y repositorio.

Responsabilidades:
  - Trocear el rango solicitado según `max_bars_per_request` del proveedor.
  - Reanudar desde el watermark tras una interrupción.
  - Reintentar errores transitorios, NO reintentar violaciones de contrato.
  - Registrar cada lote con su procedencia.

Lo que NO hace, y es deliberado:
  - No limpia datos, no rellena huecos, no detecta outliers. Eso es el Módulo
    02. Un Data Engine que "arregla" datos silenciosamente hace imposible
    saber qué entregó realmente el proveedor.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from qq_core.domain.enums import Timeframe
from qq_core.ports.data_provider import (
    DataProvider,
    ProviderContractError,
    ProviderError,
)
from qq_core.storage.repository import BarRepository, UpsertReport

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IngestionRequest:
    """Petición de ingesta de una serie.

    Attributes:
        provider_symbol: Símbolo en el dialecto del proveedor.
        canonical_symbol: Símbolo canónico interno.
        timeframe: Resolución a ingestar.
        start: Inicio UTC inclusivo.
        end: Fin UTC exclusivo.
        resume: Si `True`, empieza desde el watermark si es posterior a
            `start`. Poner `False` fuerza una reingesta completa del rango,
            que es la forma de detectar revisiones del proveedor.
    """

    provider_symbol: str
    canonical_symbol: str
    timeframe: Timeframe
    start: datetime
    end: datetime
    resume: bool = True


@dataclass
class IngestionResult:
    """Resultado de una ingesta.

    Attributes:
        request: Petición original.
        report: Conteos agregados de escritura.
        chunks_processed: Número de peticiones al proveedor.
        failed_chunks: Rangos que no se pudieron ingestar, con su motivo. Que
            existan no aborta el resto: se ingesta lo que se puede y se reporta
            lo que falta, en lugar de dejar la serie a medias en silencio.
    """

    request: IngestionRequest
    report: UpsertReport = field(default_factory=UpsertReport)
    chunks_processed: int = 0
    failed_chunks: list[tuple[datetime, datetime, str]] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return not self.failed_chunks


class IngestionService:
    """Orquestador de ingesta para un proveedor y un repositorio.

    Args:
        provider: Adaptador que cumple el puerto `DataProvider`.
        repository: Persistencia que cumple el puerto `BarRepository`.
        max_retries: Reintentos por chunk ante errores transitorios.
        backoff_seconds: Base del backoff exponencial entre reintentos.
        sleeper: Función de espera. Inyectada para que los tests no duerman.
    """

    def __init__(
        self,
        provider: DataProvider,
        repository: BarRepository,
        max_retries: int = 3,
        backoff_seconds: float = 1.0,
        sleeper=time.sleep,
    ) -> None:
        self._provider = provider
        self._repo = repository
        self._max_retries = max_retries
        self._backoff = backoff_seconds
        self._sleep = sleeper

    def ingest(self, request: IngestionRequest) -> IngestionResult:
        """Ejecuta una ingesta completa, troceada y reanudable.

        Args:
            request: Serie y rango a ingestar.

        Returns:
            Resultado con conteos y chunks fallidos.

        Raises:
            ValueError: Rango inválido o timeframe no soportado por el
                proveedor. Se falla rápido en lugar de ingestar parcialmente.
        """
        self._validate(request)
        effective_start = self._effective_start(request)
        result = IngestionResult(request=request)

        if effective_start >= request.end:
            logger.info(
                "Nada que ingestar para %s %s: watermark al día",
                request.canonical_symbol,
                request.timeframe.value,
            )
            return result

        for chunk_start, chunk_end in self._chunks(effective_start, request):
            result.chunks_processed += 1
            try:
                report = self._ingest_chunk(request, chunk_start, chunk_end)
                result.report = result.report.merged_with(report)
            except (ProviderError, ProviderContractError) as exc:
                logger.error(
                    "Chunk fallido %s..%s para %s: %s",
                    chunk_start.isoformat(),
                    chunk_end.isoformat(),
                    request.canonical_symbol,
                    exc,
                )
                result.failed_chunks.append((chunk_start, chunk_end, str(exc)))

        if result.report.revised:
            logger.warning(
                "%s barras REVISADAS por el proveedor en %s %s. "
                "Los backtests previos sobre esta serie ya no reproducen.",
                result.report.revised,
                request.canonical_symbol,
                request.timeframe.value,
            )
        return result

    def _validate(self, request: IngestionRequest) -> None:
        """Comprueba precondiciones antes de tocar la red."""
        if request.start.tzinfo is None or request.end.tzinfo is None:
            raise ValueError("start y end deben ser timezone-aware (UTC)")
        if request.start >= request.end:
            raise ValueError(f"Rango inválido: {request.start} >= {request.end}")
        caps = self._provider.capabilities
        if request.timeframe not in caps.timeframes:
            raise ValueError(
                f"{caps.source.value} no soporta {request.timeframe.value}"
            )

    def _effective_start(self, request: IngestionRequest) -> datetime:
        """Aplica el watermark si se pidió reanudar.

        Se retoma desde `watermark + 1 timeframe`, no desde el watermark, para
        no volver a pedir una barra que ya está. Es seguro porque el UPSERT es
        idempotente, pero ahorra cuota del proveedor.
        """
        if not request.resume:
            return request.start
        watermark = self._repo.get_watermark(
            request.canonical_symbol,
            request.timeframe,
            self._provider.capabilities.source,
        )
        if watermark is None:
            return request.start
        return max(request.start, watermark + timedelta(seconds=request.timeframe.seconds))

    def _chunks(self, start: datetime, request: IngestionRequest):
        """Divide el rango en tramos que quepan en una petición.

        Yields:
            Tuplas `(chunk_start, chunk_end)` semiabiertas y contiguas.
        """
        span = timedelta(
            seconds=request.timeframe.seconds
            * self._provider.capabilities.max_bars_per_request
        )
        cursor = start
        while cursor < request.end:
            chunk_end = min(cursor + span, request.end)
            yield cursor, chunk_end
            cursor = chunk_end

    def _ingest_chunk(
        self, request: IngestionRequest, start: datetime, end: datetime
    ) -> UpsertReport:
        """Ingesta un tramo con reintentos ante fallos transitorios.

        Raises:
            ProviderContractError: Nunca se reintenta. Una respuesta malformada
                seguirá malformada y reintentar solo consume cuota.
            ProviderError: Se propaga tras agotar los reintentos.
        """
        last_error: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                fetched = self._provider.fetch_bars(
                    provider_symbol=request.provider_symbol,
                    canonical_symbol=request.canonical_symbol,
                    timeframe=request.timeframe,
                    start=start,
                    end=end,
                )
                return self._repo.upsert_bars(fetched.bars, fetched.provenance)
            except ProviderContractError:
                raise
            except ProviderError as exc:
                last_error = exc
                if attempt < self._max_retries:
                    delay = self._backoff * (2 ** (attempt - 1))
                    logger.warning(
                        "Intento %s/%s falló (%s); reintentando en %.1fs",
                        attempt,
                        self._max_retries,
                        exc,
                        delay,
                    )
                    self._sleep(delay)
        assert last_error is not None
        raise last_error


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """Helper para construir datetimes UTC aware sin repetir `tzinfo=`."""
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
