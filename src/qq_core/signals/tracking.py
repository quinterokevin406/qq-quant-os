"""Seguimiento de señales: ¿sigue siendo válida la entrada?

EL PROBLEMA QUE RESUELVE
------------------------
El sistema emite una señal fuerte de compra con horizonte de tres meses. El
operador entra. Pasan dos semanas. ¿Sigue siendo válida esa posición?

Sin este módulo, el operador tendría que volver a mirar la señal del día y
adivinar si se refiere a lo mismo. Con él, cada señal viva se reevalúa a
diario y se informa de si:

  - La convicción se mantiene, ha subido o se ha debilitado.
  - La dirección sigue siendo la misma o se ha girado.
  - El precio se acerca al objetivo o a la invalidación.
  - El horizonte previsto se está agotando.

DISTINCIÓN IMPORTANTE
---------------------
Esto NO es una predicción. Es la reevaluación de la misma lógica con datos
más recientes. Si una señal se debilita no significa que vaya a perder:
significa que las condiciones que la justificaban ya no se cumplen con la
misma claridad.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

import pandas as pd

from qq_core.domain.signal import Direction, Signal, SignalStrength


class SignalHealth(StrEnum):
    """Estado de una señal en seguimiento."""

    STRENGTHENED = "reforzada"
    """La convicción ha aumentado desde la entrada."""

    HOLDING = "se mantiene"
    """Misma dirección y convicción similar."""

    WEAKENING = "debilitándose"
    """Misma dirección pero menor convicción."""

    REVERSED = "invertida"
    """La estrategia ahora indica lo contrario. Revisar la posición."""

    CLOSED = "cerrada"
    """La estrategia ya no indica posición en este instrumento."""

    EXPIRED = "horizonte agotado"
    """Se ha superado el tiempo previsto de mantenimiento."""


_ORDEN = {SignalStrength.WEAK: 1, SignalStrength.MODERATE: 2, SignalStrength.STRONG: 3}


@dataclass
class SignalStatus:
    """Estado actual de una señal emitida días atrás.

    Attributes:
        symbol: Instrumento.
        strategy: Estrategia que la generó.
        health: Diagnóstico del estado.
        original_direction: Dirección cuando se emitió.
        current_direction: Dirección que indica hoy la misma estrategia.
        original_strength: Convicción original.
        current_strength: Convicción actual.
        entry_price: Precio de entrada de referencia.
        current_price: Último precio.
        stop_price: Nivel de invalidación vigente.
        target_price: Objetivo.
        days_elapsed: Días transcurridos desde la señal.
        days_expected: Días previstos por el horizonte.
        progress_to_target_pct: Avance hacia el objetivo, en porcentaje.
        distance_to_stop_pct: Distancia restante hasta la invalidación.
        unrealized_pct: Resultado no realizado.
        message: Explicación legible del estado.
    """

    symbol: str
    strategy: str
    health: SignalHealth
    original_direction: Direction
    current_direction: Direction
    original_strength: SignalStrength
    current_strength: SignalStrength
    entry_price: Decimal
    current_price: Decimal
    stop_price: Decimal | None
    target_price: Decimal | None
    days_elapsed: int
    days_expected: int
    progress_to_target_pct: float | None
    distance_to_stop_pct: float | None
    unrealized_pct: float
    message: str

    @property
    def needs_attention(self) -> bool:
        """Si el operador debería revisar esta posición hoy."""
        return self.health in (
            SignalHealth.REVERSED,
            SignalHealth.CLOSED,
            SignalHealth.EXPIRED,
        )

    def to_row(self) -> dict[str, Any]:
        return {
            "Instrumento": self.symbol,
            "Estrategia": self.strategy,
            "Estado": self.health.value,
            "Dirección original": self.original_direction.value,
            "Dirección hoy": self.current_direction.value,
            "Convicción original": self.original_strength.value,
            "Convicción hoy": self.current_strength.value,
            "Precio entrada": float(self.entry_price),
            "Precio actual": float(self.current_price),
            "Resultado %": round(self.unrealized_pct, 2),
            "Avance al objetivo %": (
                round(self.progress_to_target_pct, 1)
                if self.progress_to_target_pct is not None else None
            ),
            "Margen al stop %": (
                round(self.distance_to_stop_pct, 1)
                if self.distance_to_stop_pct is not None else None
            ),
            "Días": f"{self.days_elapsed} de {self.days_expected}",
            "Comentario": self.message,
        }


def track_signal(
    original: Signal,
    current: Signal | None,
    current_price: Decimal,
    today: date | None = None,
) -> SignalStatus:
    """Reevalúa una señal emitida días atrás.

    Args:
        original: Señal tal como se emitió.
        current: Señal que la misma estrategia produce hoy sobre el mismo
            instrumento. `None` si ya no genera ninguna.
        current_price: Último precio conocido.
        today: Fecha de referencia, para pruebas.

    Returns:
        Estado de la señal con un diagnóstico legible.
    """
    hoy = today or date.today()
    transcurridos = (hoy - original.as_of.date()).days
    previstos = max(original.horizon.expected_days, 1)

    direccion_hoy = current.direction if current else Direction.FLAT
    conviccion_hoy = current.strength if current else SignalStrength.WEAK

    # Resultado no realizado desde el precio de entrada.
    movimiento = float(current_price - original.entry_price)
    if original.direction is Direction.SHORT:
        movimiento = -movimiento
    resultado = (
        movimiento / float(original.entry_price) * 100
        if original.entry_price else 0.0
    )

    avance = None
    if original.target_price is not None and original.entry_price != original.target_price:
        recorrido_total = abs(float(original.target_price - original.entry_price))
        recorrido_hecho = movimiento
        avance = max(0.0, min(150.0, recorrido_hecho / recorrido_total * 100))

    margen = None
    if original.stop_price is not None and current_price:
        distancia = abs(float(current_price - original.stop_price))
        margen = distancia / float(current_price) * 100

    salud, mensaje = _evaluar(
        original, direccion_hoy, conviccion_hoy, transcurridos, previstos, resultado
    )

    return SignalStatus(
        symbol=original.symbol,
        strategy=original.strategy_label or original.strategy,
        health=salud,
        original_direction=original.direction,
        current_direction=direccion_hoy,
        original_strength=original.strength,
        current_strength=conviccion_hoy,
        entry_price=original.entry_price,
        current_price=current_price,
        stop_price=original.stop_price,
        target_price=original.target_price,
        days_elapsed=transcurridos,
        days_expected=previstos,
        progress_to_target_pct=avance,
        distance_to_stop_pct=margen,
        unrealized_pct=resultado,
        message=mensaje,
    )


def _evaluar(
    original: Signal,
    direccion_hoy: Direction,
    conviccion_hoy: SignalStrength,
    transcurridos: int,
    previstos: int,
    resultado: float,
) -> tuple[SignalHealth, str]:
    """Determina el estado de la señal y redacta el comentario."""
    if direccion_hoy is Direction.FLAT:
        return SignalHealth.CLOSED, (
            f"La estrategia ya no indica posición en este instrumento. Las "
            f"condiciones que justificaron la entrada han dejado de cumplirse. "
            f"Resultado actual: {resultado:+.1f}%."
        )

    if direccion_hoy is not original.direction:
        return SignalHealth.REVERSED, (
            f"**Atención:** la estrategia ahora indica la dirección contraria "
            f"({direccion_hoy.value}). La hipótesis original ya no se sostiene. "
            f"Resultado actual: {resultado:+.1f}%."
        )

    if transcurridos > previstos * 1.5:
        return SignalHealth.EXPIRED, (
            f"Han pasado {transcurridos} días frente a los {previstos} previstos "
            f"por el horizonte de la estrategia. La señal sigue vigente, pero "
            f"conviene revisar si mantenerla compensa el coste de financiación "
            f"acumulado."
        )

    original_nivel = _ORDEN[original.strength]
    actual_nivel = _ORDEN[conviccion_hoy]

    if actual_nivel > original_nivel:
        return SignalHealth.STRENGTHENED, (
            f"La señal se ha reforzado: pasó de convicción "
            f"{original.strength.value} a {conviccion_hoy.value}. Las "
            f"condiciones son ahora más claras que al entrar. Resultado: "
            f"{resultado:+.1f}%."
        )

    if actual_nivel < original_nivel:
        return SignalHealth.WEAKENING, (
            f"La señal se debilita: pasó de {original.strength.value} a "
            f"{conviccion_hoy.value}. Mantiene la dirección, pero con menos "
            f"claridad que al entrar. No implica que vaya a perder; sí que "
            f"conviene vigilarla. Resultado: {resultado:+.1f}%."
        )

    return SignalHealth.HOLDING, (
        f"Sin cambios relevantes: misma dirección y convicción "
        f"{conviccion_hoy.value}. Día {transcurridos} de {previstos} previstos. "
        f"Resultado: {resultado:+.1f}%."
    )


def track_many(
    originals: list[Signal],
    current_signals: dict[tuple[str, str], Signal],
    prices: dict[str, Decimal],
    today: date | None = None,
) -> list[SignalStatus]:
    """Reevalúa varias señales a la vez.

    Args:
        originals: Señales emitidas previamente y aún en seguimiento.
        current_signals: Señales de hoy, indexadas por (símbolo, estrategia).
        prices: Último precio de cada instrumento.
        today: Fecha de referencia.

    Returns:
        Estados ordenados poniendo primero las que requieren atención.
    """
    estados = []
    for señal in originals:
        precio = prices.get(señal.symbol)
        if precio is None:
            continue
        actual = current_signals.get((señal.symbol, señal.strategy))
        estados.append(track_signal(señal, actual, precio, today))

    prioridad = {
        SignalHealth.REVERSED: 0,
        SignalHealth.CLOSED: 1,
        SignalHealth.EXPIRED: 2,
        SignalHealth.WEAKENING: 3,
        SignalHealth.HOLDING: 4,
        SignalHealth.STRENGTHENED: 5,
    }
    return sorted(estados, key=lambda e: prioridad[e.health])
