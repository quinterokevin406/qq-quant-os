"""Estimación de la probabilidad de alcanzar el objetivo antes que el stop.

QUÉ RESPONDE
------------
"De estas seis operaciones que tengo en seguimiento, ¿cuál tiene más
probabilidad de llegar a su objetivo?"

CÓMO SE ESTIMA, Y POR QUÉ ASÍ
-------------------------------
Se combinan dos fuentes independientes y se mezclan según cuánta muestra haya
detrás de cada una:

**1. Geometría del movimiento.** Si el objetivo está al doble de distancia que
el stop, alcanzarlo antes es menos probable, por pura aritmética. Bajo un
paseo aleatorio sin deriva, la probabilidad de tocar el objetivo antes que el
stop es:

    P = distancia_al_stop / (distancia_al_stop + distancia_al_objetivo)

Es un resultado clásico y no requiere estimar nada: sólo mide la forma de la
operación. Es el ancla del cálculo.

**2. Histórico de la combinación.** El porcentaje de veces que esa estrategia,
en ese instrumento, alcanzó su objetivo. Aporta información real, pero es una
estimación ruidosa cuando hay pocas operaciones.

Las dos se mezclan con peso `n / (n + 30)`: con poca muestra manda la
geometría, que no puede equivocarse porque no estima nada; con mucha muestra
manda el histórico.

**3. Ajuste por avance.** Una operación que ya recorrió el 60% del camino hacia
el objetivo tiene más probabilidad de completarlo que una recién abierta. Se
recalcula la geometría desde el precio actual, no desde la entrada.

LO QUE ESTA CIFRA NO ES
------------------------
No es una predicción. Es una estimación con margen de error amplio, construida
sobre un supuesto —movimiento sin deriva— que sabemos falso: si el mercado
tuviera deriva conocida, no haría falta ninguna estrategia.

Sirve para ORDENAR operaciones entre sí, que es para lo que se usa. No sirve
para afirmar "esta operación tiene un 68% de éxito".

Toda salida de este módulo debe presentarse con su nivel de confianza, que
depende directamente del tamaño de muestra disponible.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

K_MEZCLA = 30.0
"""Operaciones a las que el histórico pesa la mitad frente a la geometría."""


class Confidence(StrEnum):
    """Cuánta confianza merece la estimación."""

    GEOMETRIC_ONLY = "solo_geometria"
    """Sin histórico utilizable. La cifra sólo refleja la forma de la operación."""

    LOW = "baja"
    """Menos de 30 operaciones históricas comparables."""

    MEDIUM = "media"
    """Entre 30 y 100 operaciones."""

    HIGH = "alta"
    """Más de 100 operaciones."""

    @property
    def label(self) -> str:
        return {
            Confidence.GEOMETRIC_ONLY: "Sólo geometría, sin histórico",
            Confidence.LOW: "Confianza baja",
            Confidence.MEDIUM: "Confianza media",
            Confidence.HIGH: "Confianza alta",
        }[self]


@dataclass(frozen=True)
class TargetProbability:
    """Probabilidad estimada de alcanzar el objetivo antes que el stop.

    Attributes:
        probability: Probabilidad entre 0 y 1.
        geometric: Componente geométrica, desde el precio actual.
        historical: Porcentaje histórico de aciertos. `None` si no hay muestra.
        n_trades: Operaciones históricas comparables.
        weight_historical: Peso dado al histórico en la mezcla.
        confidence: Nivel de confianza de la estimación.
        progress_pct: Avance hacia el objetivo, en porcentaje.
        reward_risk: Relación entre distancia al objetivo y distancia al stop.
        explanation: Explicación en lenguaje llano.
    """

    probability: float
    geometric: float
    historical: float | None
    n_trades: int
    weight_historical: float
    confidence: Confidence
    progress_pct: float
    reward_risk: float
    explanation: str

    @property
    def expected_value_r(self) -> float:
        """Valor esperado en múltiplos del riesgo.

        Positivo significa que, con esta probabilidad y esta relación
        beneficio/riesgo, la operación tiene esperanza favorable.

            VE = P x beneficio - (1 - P) x 1
        """
        return self.probability * self.reward_risk - (1.0 - self.probability)


def estimate_target_probability(
    entry_price: Decimal,
    current_price: Decimal,
    stop_price: Decimal,
    target_price: Decimal | None,
    direction: str,
    historical_hit_rate: float | None = None,
    n_trades: int = 0,
) -> TargetProbability | None:
    """Estima la probabilidad de alcanzar el objetivo antes que el stop.

    Args:
        entry_price: Precio de entrada.
        current_price: Precio actual.
        stop_price: Nivel de invalidación.
        target_price: Objetivo. Si no existe, no se puede estimar.
        direction: 'long' o 'short'.
        historical_hit_rate: Proporción histórica de veces que esta combinación
            alcanzó su objetivo, entre 0 y 1.
        n_trades: Operaciones históricas sobre las que se calculó.

    Returns:
        Estimación, o `None` si la operación no tiene objetivo definido o los
        precios son incoherentes.
    """
    if target_price is None:
        return None

    es_largo = direction.lower() in ("long", "compra")
    signo = Decimal("1") if es_largo else Decimal("-1")

    # Distancias DESDE EL PRECIO ACTUAL, no desde la entrada: una operación que
    # ya avanzó tiene el objetivo más cerca y el stop más lejos.
    al_objetivo = (target_price - current_price) * signo
    al_stop = (current_price - stop_price) * signo

    if al_objetivo <= 0:
        # Ya alcanzó el objetivo.
        return TargetProbability(
            probability=1.0, geometric=1.0, historical=historical_hit_rate,
            n_trades=n_trades, weight_historical=0.0,
            confidence=Confidence.GEOMETRIC_ONLY, progress_pct=100.0,
            reward_risk=0.0,
            explanation="El precio ya alcanzó o superó el objetivo.",
        )
    if al_stop <= 0:
        return TargetProbability(
            probability=0.0, geometric=0.0, historical=historical_hit_rate,
            n_trades=n_trades, weight_historical=0.0,
            confidence=Confidence.GEOMETRIC_ONLY, progress_pct=0.0,
            reward_risk=0.0,
            explanation="El precio ya alcanzó o perforó la invalidación.",
        )

    d_obj = float(al_objetivo)
    d_stop = float(al_stop)

    # Ancla: bajo movimiento sin deriva, la probabilidad de tocar antes un nivel
    # es proporcional a lo cerca que esté.
    geometrica = d_stop / (d_stop + d_obj)

    # Avance recorrido desde la entrada.
    recorrido_total = float(abs(target_price - entry_price))
    recorrido_hecho = float((current_price - entry_price) * signo)
    avance = (
        max(0.0, min(100.0, recorrido_hecho / recorrido_total * 100.0))
        if recorrido_total > 0 else 0.0
    )

    # Mezcla con el histórico según la muestra disponible.
    if historical_hit_rate is None or n_trades <= 0:
        probabilidad = geometrica
        peso = 0.0
        confianza = Confidence.GEOMETRIC_ONLY
    else:
        peso = n_trades / (n_trades + K_MEZCLA)
        probabilidad = geometrica * (1.0 - peso) + historical_hit_rate * peso
        if n_trades < 30:
            confianza = Confidence.LOW
        elif n_trades < 100:
            confianza = Confidence.MEDIUM
        else:
            confianza = Confidence.HIGH

    probabilidad = min(max(probabilidad, 0.01), 0.99)
    beneficio_riesgo = d_obj / d_stop

    if peso == 0.0:
        detalle = (
            "Sin histórico comparable: la cifra sólo refleja la forma de la "
            "operación."
        )
    else:
        detalle = (
            f"Histórico: {historical_hit_rate * 100:.0f}% de acierto en "
            f"{n_trades} operaciones, con peso del {peso * 100:.0f}%."
        )

    explicacion = (
        f"El objetivo está a {d_obj:.2f} y la invalidación a {d_stop:.2f} del "
        f"precio actual (relación {beneficio_riesgo:.2f} a 1). Sólo por esa "
        f"geometría, la probabilidad sería del {geometrica * 100:.0f}%. "
        f"{detalle} Avance hacia el objetivo: {avance:.0f}%."
    )

    return TargetProbability(
        probability=probabilidad,
        geometric=geometrica,
        historical=historical_hit_rate,
        n_trades=n_trades,
        weight_historical=peso,
        confidence=confianza,
        progress_pct=avance,
        reward_risk=beneficio_riesgo,
        explanation=explicacion,
    )
