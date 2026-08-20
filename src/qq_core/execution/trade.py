"""Registro de operaciones reales (Módulo 13 — Manual Execution Tracker).

QUÉ HACE ESTE MÓDULO Y POR QUÉ EXISTE
--------------------------------------
El sistema recomienda; el operador decide y ejecuta a mano en MetaTrader. Entre
esas dos cosas hay una brecha que casi ningún sistema mide:

  - El operador no ejecuta todas las señales. Ignora algunas.
  - Cuando ejecuta, lo hace más tarde y a un precio distinto del recomendado.
  - A veces cierra antes o después de lo que la estrategia indicaba.

Esa brecha —el *implementation shortfall*— puede ser mayor que la diferencia
entre una estrategia buena y una mediocre. Medirla es la ventaja concreta de
tener ejecución manual: un sistema automático no puede observarla porque no
existe.

DISTINCIÓN FUNDAMENTAL
----------------------
`Trade` (en `portfolio.simulator`) es una operación HIPOTÉTICA: lo que el
sistema habría hecho sobre datos históricos.

`RealTrade` (aquí) es una operación EJECUTADA: dinero real, en una cuenta real.
Nunca deben mezclarse en el mismo informe sin decir cuál es cuál, porque
confundirlas convierte una simulación en una promesa.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from qq_core.domain.signal import Direction


class TradeStatus(StrEnum):
    """Estado de una operación real."""

    OPEN = "abierta"
    CLOSED = "cerrada"


class ExitReason(StrEnum):
    """Motivo por el que se cerró una posición.

    Se registra porque distingue entre disciplina y decisión discrecional. Un
    operador que cierra por «decisión propia» de forma sistemática está
    operando otro sistema distinto del que dice seguir, y eso debe poder verse
    en los datos.
    """

    TARGET = "objetivo alcanzado"
    STOP = "invalidación tocada"
    SIGNAL = "cambio de señal"
    MANUAL = "decisión del operador"
    OTHER = "otro"


class RealTrade(BaseModel):
    """Operación ejecutada realmente en el bróker.

    Attributes:
        trade_id: Identificador interno.
        symbol: Instrumento canónico.
        direction: Comprada o vendida.
        units: Unidades ejecutadas. El operador puede haber operado un tamaño
            distinto del recomendado; se registra el real.
        entry_date: Fecha de apertura.
        entry_price: Precio de entrada REAL, el del bróker.
        exit_date: Fecha de cierre. `None` si sigue abierta.
        exit_price: Precio de salida real.
        stop_price: Nivel de invalidación fijado al abrir.
        target_price: Objetivo fijado al abrir.
        exit_reason: Por qué se cerró.
        strategy: Estrategia que originó la señal, si la hubo.
        signal_id: Señal concreta que se siguió. `None` si el operador abrió
            la posición por decisión propia, sin señal del sistema. Distinguir
            ambos casos es esencial para evaluar el sistema por separado del
            criterio del operador.
        signal_entry_price: Precio que el sistema recomendaba. Comparado con
            `entry_price` da el deslizamiento de ejecución.
        signal_date: Fecha del dato con el que se generó la señal. Comparada
            con `entry_date` da el retraso de decisión.
        notes: Observaciones del operador.
        broker_ticket: Número de operación en MetaTrader, para conciliación.
    """

    model_config = ConfigDict(extra="forbid")

    trade_id: str = Field(min_length=1, max_length=64)
    symbol: str = Field(min_length=1, max_length=32)
    direction: Direction
    units: Decimal = Field(gt=0)

    entry_date: date
    entry_price: Decimal = Field(gt=0)

    exit_date: date | None = None
    exit_price: Decimal | None = Field(default=None, gt=0)

    stop_price: Decimal | None = Field(default=None, gt=0)
    target_price: Decimal | None = Field(default=None, gt=0)
    exit_reason: ExitReason | None = None

    strategy: str = Field(default="", max_length=64)
    signal_id: str | None = None
    signal_entry_price: Decimal | None = Field(default=None, gt=0)
    signal_date: date | None = None

    notes: str = Field(default="", max_length=500)
    broker_ticket: str = Field(default="", max_length=32)
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("direction")
    @classmethod
    def _no_flat(cls, v: Direction) -> Direction:
        if v is Direction.FLAT:
            raise ValueError("una operación real no puede tener dirección 'flat'")
        return v

    @model_validator(mode="after")
    def _coherencia_de_cierre(self) -> "RealTrade":
        """Una operación cerrada necesita fecha y precio de salida."""
        if self.exit_date is not None and self.exit_price is None:
            raise ValueError("una operación cerrada requiere precio de salida")
        if self.exit_price is not None and self.exit_date is None:
            raise ValueError("un precio de salida requiere fecha de cierre")
        if self.exit_date is not None and self.exit_date < self.entry_date:
            raise ValueError("la fecha de cierre no puede ser anterior a la de entrada")
        return self

    # ------------------------------------------------------------------ #
    # Estado y resultado
    # ------------------------------------------------------------------ #

    @property
    def status(self) -> TradeStatus:
        return TradeStatus.CLOSED if self.exit_date else TradeStatus.OPEN

    @property
    def is_open(self) -> bool:
        return self.exit_date is None

    def pnl(self, current_price: Decimal | None = None) -> Decimal:
        """Resultado en divisa de cuenta.

        Args:
            current_price: Precio actual, para valorar posiciones abiertas.

        Returns:
            Resultado realizado si está cerrada, no realizado si está abierta,
            y cero si está abierta y no se proporciona precio actual.
        """
        precio_final = self.exit_price or current_price
        if precio_final is None:
            return Decimal(0)
        movimiento = precio_final - self.entry_price
        if self.direction is Direction.SHORT:
            movimiento = -movimiento
        return movimiento * self.units

    def return_pct(self, current_price: Decimal | None = None) -> float:
        """Resultado en porcentaje sobre el capital comprometido."""
        base = self.units * self.entry_price
        if base == 0:
            return 0.0
        return float(self.pnl(current_price) / base * 100)

    @property
    def notional(self) -> Decimal:
        """Valor nocional de la posición al entrar."""
        return self.units * self.entry_price

    @property
    def days_held(self) -> int:
        """Días naturales que se mantuvo la posición."""
        fin = self.exit_date or date.today()
        return (fin - self.entry_date).days

    # ------------------------------------------------------------------ #
    # Calidad de ejecución — el valor propio de este módulo
    # ------------------------------------------------------------------ #

    @property
    def followed_signal(self) -> bool:
        """Si la operación proviene de una señal del sistema."""
        return self.signal_id is not None

    @property
    def slippage(self) -> Decimal | None:
        """Diferencia entre el precio real de entrada y el recomendado.

        Positivo significa que se entró PEOR que lo recomendado: más caro en
        una compra, más barato en una venta. Es un coste que no aparece en
        ningún backtest y que sólo puede medirse comparando con la ejecución
        real.
        """
        if self.signal_entry_price is None:
            return None
        diferencia = self.entry_price - self.signal_entry_price
        if self.direction is Direction.SHORT:
            diferencia = -diferencia
        return diferencia

    @property
    def slippage_pct(self) -> float | None:
        """Deslizamiento en porcentaje del precio recomendado."""
        desliz = self.slippage
        if desliz is None or self.signal_entry_price == 0:
            return None
        return round(float(desliz / self.signal_entry_price * 100), 4)

    @property
    def decision_lag_days(self) -> int | None:
        """Días entre la generación de la señal y la ejecución real.

        Un retraso sistemático indica un problema de proceso: el sistema
        recomienda con el cierre de una sesión y la ejecución debería ocurrir
        en la siguiente. Retrasos mayores significan que se está operando con
        información envejecida.
        """
        if self.signal_date is None:
            return None
        return (self.entry_date - self.signal_date).days

    @property
    def respected_stop(self) -> bool | None:
        """Si el operador respetó el nivel de invalidación que fijó.

        `False` cuando la posición se cerró por decisión propia habiendo un
        stop definido que no llegó a tocarse, o cuando se dejó correr una
        pérdida más allá del nivel establecido. Es la métrica de disciplina.
        """
        if self.stop_price is None or self.exit_price is None:
            return None
        if self.exit_reason is ExitReason.STOP:
            return True
        if self.direction is Direction.LONG:
            return self.exit_price > self.stop_price
        return self.exit_price < self.stop_price

    def to_row(self, current_price: Decimal | None = None) -> dict[str, Any]:
        """Representación plana para tablas."""
        return {
            "Instrumento": self.symbol,
            "Dirección": "COMPRA" if self.direction is Direction.LONG else "VENTA",
            "Estado": self.status.value,
            "Unidades": float(self.units),
            "Entrada": self.entry_date.isoformat(),
            "Precio entrada": float(self.entry_price),
            "Salida": self.exit_date.isoformat() if self.exit_date else "—",
            "Precio salida": float(self.exit_price) if self.exit_price else None,
            "Días": self.days_held,
            "Resultado $": round(float(self.pnl(current_price)), 2),
            "Resultado %": round(self.return_pct(current_price), 2),
            "Estrategia": self.strategy or "Sin señal",
            "Siguió señal": "Sí" if self.followed_signal else "No",
            "Deslizamiento %": self.slippage_pct,
            "Retraso (días)": self.decision_lag_days,
            "Motivo salida": self.exit_reason.value if self.exit_reason else "—",
            "Notas": self.notes,
        }


def build_trade_id(symbol: str, entry_date: date, ticket: str = "") -> str:
    """Construye un identificador de operación.

    Incluye el número de operación del bróker cuando existe, porque es lo que
    permite conciliar el registro con el extracto de la cuenta.
    """
    base = f"{symbol}:{entry_date.isoformat()}"
    return f"{base}:{ticket}" if ticket else base
