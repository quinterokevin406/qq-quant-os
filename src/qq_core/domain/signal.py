"""Contrato de señal — la salida del sistema.

CONTRATO CONGELADO (v2.0.0)
----------------------------
Junto con `Bar` e `Instrument`, `Signal` es uno de los tres contratos que no
pueden cambiar sin un ADR. Es lo que atraviesa toda la plataforma: lo producen
las estrategias, lo consume el constructor de portafolio, lo filtra el motor de
riesgo, lo muestra el panel y lo audita el registro de decisiones.

CAMBIOS DE LA v2.0.0 RESPECTO A LA v1.0.0
------------------------------------------
La versión anterior daba dirección y un precio de referencia. Resultaba
insuficiente para operar: el operador recibía "comprar US500 a 5.000" sin saber
cuánto tiempo mantener la posición, dónde tomar beneficio, ni si ese 5.000 era
el precio al que se detectó la señal o al que debía entrar.

Se añaden tres bloques:

  1. **Horizonte de mantenimiento** (`horizon`). Cada estrategia declara cuánto
     tiempo espera mantener la posición. No es un dato decorativo: determina si
     el coste de financiación es relevante. Una posición intradía no paga swap;
     una de un mes paga treinta noches.

  2. **Plan de operación completo** (`entry_price`, `target_price`,
     `stop_price`). Los tres precios explícitos y separados.

  3. **Separación entre observación y ejecución**. `observed_price` es el
     precio con el que se detectó la señal —el último cierre disponible—.
     `entry_price` es el precio al que se recomienda entrar, que es distinto
     porque la ejecución ocurre en la sesión siguiente. Confundirlos es la
     forma más común de creerse un backtest que no se puede reproducir.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

SIGNAL_SCHEMA_VERSION = "2.0.0"
"""Versión del esquema de señal. Cambiarla requiere ADR."""


class Direction(StrEnum):
    """Dirección de la señal."""

    LONG = "long"
    """Comprado: se beneficia de subidas."""

    SHORT = "short"
    """Vendido: se beneficia de bajadas."""

    FLAT = "flat"
    """Sin posición. NO es ausencia de señal: es la decisión explícita de no
    estar en el mercado."""


class SignalStrength(StrEnum):
    """Convicción de la estrategia sobre la señal.

    Deliberadamente cualitativa y de tres niveles. Una escala numérica fina
    sugeriría una precisión que las estrategias no tienen, e invitaría a
    dimensionar posiciones proporcionalmente a un número que es ruido. El
    tamaño lo decide el motor de riesgo a partir de la volatilidad.
    """

    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class Horizon(StrEnum):
    """Tiempo previsto de mantenimiento de la posición.

    Lo declara cada estrategia según su lógica, no el operador. Una estrategia
    de cruce de medias de 200 sesiones no puede tener horizonte intradía: sus
    señales cambian cada varias semanas.

    Determina además si el coste de financiación aplica y en qué magnitud, lo
    que en instrumentos con swap del 9-13% anual cambia por completo la
    rentabilidad esperada.
    """

    INTRADAY = "intradia"
    """Se abre y cierra dentro de la misma sesión. Sin coste de financiación."""

    DAY_1 = "1_dia"
    """Una noche. Un cargo de financiación."""

    WEEK_1 = "1_semana"
    """Alrededor de cinco sesiones."""

    WEEK_2 = "2_semanas"
    """Alrededor de diez sesiones."""

    MONTH_1 = "1_mes"
    """Alrededor de veintidós sesiones."""

    MONTH_3 = "3_meses"
    """Un trimestre. Horizonte típico del seguimiento de tendencia largo."""

    @property
    def label(self) -> str:
        """Etiqueta legible para el panel y los informes."""
        return _HORIZON_LABEL[self]

    @property
    def expected_days(self) -> int:
        """Sesiones de mercado previstas. Se usa para estimar el coste de
        financiación acumulado antes de abrir la posición."""
        return _HORIZON_DAYS[self]

    @property
    def pays_financing(self) -> bool:
        """Si la posición acumula coste overnight."""
        return self is not Horizon.INTRADAY


_HORIZON_LABEL: dict[Horizon, str] = {
    Horizon.INTRADAY: "Intradía",
    Horizon.DAY_1: "1 día",
    Horizon.WEEK_1: "1 semana",
    Horizon.WEEK_2: "2 semanas",
    Horizon.MONTH_1: "1 mes",
    Horizon.MONTH_3: "3 meses",
}

_HORIZON_DAYS: dict[Horizon, int] = {
    Horizon.INTRADAY: 0,
    Horizon.DAY_1: 1,
    Horizon.WEEK_1: 5,
    Horizon.WEEK_2: 10,
    Horizon.MONTH_1: 22,
    Horizon.MONTH_3: 66,
}


class Signal(BaseModel):
    """Recomendación de una estrategia sobre un instrumento en un momento dado.

    Attributes:
        schema_version: Versión del contrato con que se generó.
        signal_id: Identificador único y determinista.
        strategy: Nombre de la estrategia que la generó.
        strategy_label: Nombre legible para el panel.
        strategy_version: Versión de la estrategia. Dos versiones pueden dar
            señales opuestas sobre el mismo dato; sin este campo no se podría
            auditar cuál se siguió.
        symbol: Instrumento canónico.
        instrument_name: Nombre legible del instrumento.
        as_of: Fecha del dato con el que se calculó. Es el cierre de la última
            barra disponible, NO el momento de generación.
        generated_at: Momento en que se ejecutó el cálculo.
        direction: Dirección recomendada.
        strength: Convicción de la estrategia.
        horizon: Tiempo previsto de mantenimiento.
        observed_price: Precio con el que se DETECTÓ la señal, es decir, el
            último cierre disponible.
        entry_price: Precio al que se recomienda ENTRAR. Distinto del anterior
            porque la ejecución ocurre en la sesión siguiente.
        target_price: Precio objetivo de toma de beneficio. `None` si la
            estrategia sale por señal en lugar de por precio.
        stop_price: Precio de invalidación. Si se alcanza, la hipótesis de la
            estrategia era incorrecta y se cierra.
        rationale: Explicación legible de por qué se generó. Obligatoria.
        exit_rule: Cómo se sale de la posición, en lenguaje llano.
        features: Valores de los indicadores que la produjeron, para poder
            reproducir el cálculo.
    """

    # `extra="forbid"` protege contra campos mal escritos. `risk_reward` es
    # una propiedad calculada, NO un campo: si se declarara como
    # `computed_field` se serializaría al guardar y sería rechazada al leer,
    # rompiendo cualquier persistencia de señales.
    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: str = SIGNAL_SCHEMA_VERSION
    signal_id: str = Field(min_length=1, max_length=128)
    strategy: str = Field(min_length=1, max_length=64)
    strategy_label: str = Field(default="", max_length=64)
    strategy_version: str = Field(min_length=1, max_length=16)
    symbol: str = Field(min_length=1, max_length=32)
    instrument_name: str = Field(default="", max_length=128)

    as_of: datetime
    generated_at: datetime

    direction: Direction
    strength: SignalStrength = SignalStrength.MODERATE
    horizon: Horizon = Horizon.WEEK_2

    observed_price: Decimal
    entry_price: Decimal
    target_price: Decimal | None = None
    stop_price: Decimal | None = None

    rationale: str = Field(min_length=1, max_length=600)
    exit_rule: str = Field(default="", max_length=300)
    features: dict[str, float] = Field(default_factory=dict)

    @field_validator("as_of", "generated_at")
    @classmethod
    def _must_be_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("las marcas de tiempo deben ser UTC aware")
        return v.astimezone(timezone.utc)

    @property
    def is_actionable(self) -> bool:
        """Si la señal implica abrir posición."""
        return self.direction is not Direction.FLAT

    @property
    def risk_reward(self) -> float | None:
        """Relación entre recorrido al objetivo y riesgo hasta la invalidación.

        Un valor de 2.0 significa que se arriesga 1 para ganar 2. Por debajo de
        1.0 la operación necesita acertar más del 50% de las veces sólo para
        quedar en equilibrio, lo que la hace poco atractiva salvo que la
        estrategia tenga una tasa de acierto demostradamente alta.
        """
        if self.target_price is None or self.stop_price is None:
            return None
        recorrido = abs(float(self.target_price - self.entry_price))
        riesgo = abs(float(self.entry_price - self.stop_price))
        return round(recorrido / riesgo, 2) if riesgo > 1e-12 else None

    @property
    def target_pct(self) -> float | None:
        """Recorrido al objetivo en porcentaje sobre el precio de entrada."""
        if self.target_price is None or self.entry_price == 0:
            return None
        movimiento = float(self.target_price - self.entry_price)
        if self.direction is Direction.SHORT:
            movimiento = -movimiento
        return round(movimiento / float(self.entry_price) * 100, 2)

    @property
    def stop_pct(self) -> float | None:
        """Distancia a la invalidación en porcentaje sobre el precio de entrada."""
        if self.stop_price is None or self.entry_price == 0:
            return None
        return round(
            abs(float(self.entry_price - self.stop_price))
            / float(self.entry_price) * 100,
            2,
        )

    def financing_cost_estimate(self, annual_rate: Decimal | None) -> float | None:
        """Coste estimado de financiación durante el horizonte previsto.

        Permite al operador saber, ANTES de abrir, cuánto le va a costar
        mantener la posición el tiempo que la estrategia espera mantenerla.

        Args:
            annual_rate: Tasa anual del instrumento en la dirección de la
                señal, en tanto por uno.

        Returns:
            Porcentaje del nocional que costará el periodo. Negativo = coste.
        """
        if annual_rate is None or not self.horizon.pays_financing:
            return 0.0
        # Los días naturales incluyen fines de semana, que también se cobran.
        dias_naturales = self.horizon.expected_days * 7 / 5
        return round(float(annual_rate) / 365 * dias_naturales * 100, 3)

    def to_row(self) -> dict[str, Any]:
        """Representación plana para tablas y exportación."""
        return {
            "Instrumento": self.symbol,
            "Nombre": self.instrument_name,
            "Señal": "COMPRA" if self.direction is Direction.LONG
                     else "VENTA" if self.direction is Direction.SHORT else "FUERA",
            "Convicción": self.strength.value,
            "Horizonte": self.horizon.label,
            "Precio observado": float(self.observed_price),
            "Precio entrada": float(self.entry_price),
            "Precio objetivo": float(self.target_price) if self.target_price else None,
            "Invalidación": float(self.stop_price) if self.stop_price else None,
            "Objetivo %": self.target_pct,
            "Riesgo %": self.stop_pct,
            "Beneficio/Riesgo": self.risk_reward,
            "Fecha dato": self.as_of.date().isoformat(),
            "Estrategia": self.strategy_label or self.strategy,
            "Salida": self.exit_rule,
            "Justificación": self.rationale,
        }


def build_signal_id(strategy: str, symbol: str, as_of: datetime) -> str:
    """Construye un identificador determinista de señal.

    Determinista a propósito: regenerar las señales del mismo día produce los
    mismos identificadores, de modo que el registro de decisiones no acumula
    duplicados si el proceso se ejecuta dos veces.
    """
    return f"{strategy}:{symbol}:{as_of.date().isoformat()}"
