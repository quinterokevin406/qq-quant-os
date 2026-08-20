"""Barra canónica OHLCV.

CONVENCIONES CONGELADAS (cambiarlas es MAJOR + ADR)
---------------------------------------------------
1. `ts` es el instante de APERTURA de la barra, no el de cierre.
   Justificación: es la convención de MT5, Stooq y la mayoría de feeds. La
   alternativa (timestamp de cierre) es más segura contra lookahead pero
   obligaría a traducir en cada adaptador, multiplicando el riesgo de error.
   La protección contra lookahead se implementa en el Módulo 03 (Feature
   Engine), que solo puede leer barras con `ts + timeframe <= now`.

2. `ts` SIEMPRE es UTC y timezone-aware. No se almacena hora local jamás.
   La hora de sesión es un feature derivado, no un atributo del dato.

3. Los precios son `Decimal`, no `float`. Un feed de 5 dígitos en FX pierde
   precisión en float64 al acumular; en PnL apalancado eso importa.

4. La clave natural es (symbol, timeframe, ts, source). Incluir `source` es
   deliberado: la misma barra de EURUSD según GBI y según Stooq son datos
   DISTINTOS y deben poder coexistir para poder compararlos. La resolución a
   una serie única es responsabilidad del Módulo 02, no del 01.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from qq_core.domain.enums import DataSource, Timeframe
from qq_core.domain.provenance import content_hash


class Bar(BaseModel):
    """Una observación OHLCV de un instrumento en una resolución temporal.

    Attributes:
        symbol: Símbolo canónico del instrumento.
        timeframe: Resolución de la barra.
        ts: Instante UTC de APERTURA de la barra.
        source: Proveedor del que procede esta observación.
        open: Precio de apertura.
        high: Precio máximo.
        low: Precio mínimo.
        close: Precio de cierre.
        volume: Volumen negociado. `None` cuando el proveedor no lo publica
            (típico en FX spot y en series macro). `None` y `0` significan
            cosas distintas y no deben confundirse.
        open_interest: Interés abierto. Solo relevante en futuros.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    timeframe: Timeframe
    ts: datetime
    source: DataSource

    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None = Field(default=None, ge=0)
    open_interest: Decimal | None = Field(default=None, ge=0)

    @field_validator("ts")
    @classmethod
    def _ts_must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ts debe ser timezone-aware; usar UTC explícito")
        return v.astimezone(timezone.utc)

    @model_validator(mode="after")
    def _ohlc_must_be_coherent(self) -> "Bar":
        """Invariante estructural: high >= max(o,c) y low <= min(o,c).

        Esto NO es control de calidad de datos (eso es el Módulo 02). Es una
        invariante del tipo: una barra que la viola no es una barra defectuosa,
        es un error de parseo del adaptador. Debe fallar en la frontera, no
        propagarse a la base de datos.
        """
        if self.high < self.low:
            raise ValueError(f"high ({self.high}) < low ({self.low})")
        if self.high < max(self.open, self.close):
            raise ValueError("high es menor que open o close")
        if self.low > min(self.open, self.close):
            raise ValueError("low es mayor que open o close")
        return self

    @model_validator(mode="after")
    def _ts_aligned_to_timeframe(self) -> "Bar":
        """La apertura debe estar alineada a la rejilla del timeframe.

        Una barra de 1h con `ts` en el minuto 37 indica que el adaptador está
        usando timestamps de cierre, o que el proveedor entregó datos
        desalineados. Ambos casos son bugs.

        Excepción: W1 no se valida contra epoch porque la semana de mercado no
        empieza el jueves (1970-01-01). La alineación semanal se verifica en el
        Módulo 02, donde se conoce el calendario de la bolsa.
        """
        if self.timeframe is Timeframe.W1:
            return self
        epoch_seconds = int(self.ts.timestamp())
        if epoch_seconds % self.timeframe.seconds != 0:
            raise ValueError(
                f"ts {self.ts.isoformat()} no está alineado a la rejilla "
                f"de {self.timeframe.value}"
            )
        return self

    @property
    def natural_key(self) -> tuple[str, str, datetime, str]:
        """Clave natural de la barra: (symbol, timeframe, ts, source)."""
        return (self.symbol, self.timeframe.value, self.ts, self.source.value)

    @property
    def content_hash(self) -> str:
        """Hash del contenido para detección de revisiones del proveedor.

        Incluye solo campos de valor, nunca metadatos mutables como
        `ingested_at`. Dos ingestas del mismo dato producen el mismo hash; una
        revisión del proveedor produce uno distinto.
        """
        return content_hash(
            {
                "symbol": self.symbol,
                "timeframe": self.timeframe.value,
                "ts": self.ts.isoformat(),
                "source": self.source.value,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "open_interest": self.open_interest,
            }
        )
