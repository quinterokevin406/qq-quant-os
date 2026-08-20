"""Signal Quality Score: puntuación 0-100 de cada señal.

QUÉ PUNTÚA
----------
La calidad de una señal a partir de las estadísticas históricas de su
combinación estrategia-instrumento-dirección: expectativa por operación,
profit factor, Sharpe, payoff y caída máxima.

La puntuación es la base del reparto de capital: determina qué señal es mejor
que otra y, por tanto, cuánto riesgo merece cada una.

DECISIÓN DE ALCANCE (v1.12)
----------------------------
Hasta la v1.11 esta puntuación exigía el informe de validación estadística del
Módulo 05: una combinación que no superaba la corrección por selección múltiple
recibía cero automáticamente.

Por decisión de negocio, esa barrera se ha retirado. La puntuación se calcula
ahora únicamente sobre las estadísticas históricas.

Consecuencia que debe conocerse: las estadísticas históricas de estas
combinaciones proceden de una búsqueda entre 198 candidatos. Parte de lo que
distingue a las mejores es mérito y parte es el azar de haber mirado muchas
veces. Esta puntuación no separa una cosa de la otra. El módulo `validation`
sigue en el repositorio y puede volver a conectarse si se decide medirlo.

LO QUE SÍ SE CONSERVA
----------------------
Dos salvaguardas que no son "validación" sino higiene estadística elemental, y
sin las cuales el reparto de capital daría resultados absurdos:

**Mínimo de operaciones.** Una casilla con 8 operaciones no se puntúa. Con
retroceso de nivel cuando la casilla específica no tiene muestra suficiente.

**Encogimiento por tamaño de muestra.** Una casilla con 35 operaciones se mezcla
con la media global. Sin esto, una combinación con pocas operaciones
afortunadas recibiría más capital que otra con trescientas y buen historial,
que es exactamente lo contrario de lo que debe hacer un motor de reparto.

EL PROBLEMA DE TAMAÑO DE MUESTRA Y CÓMO SE RESUELVE
-----------------------------------------------------
Puntuar la combinación estrategia x activo x dirección x horizonte x régimen
son del orden de 1.650 casillas. Con unos pocos miles de operaciones en diez
años, la mayoría tendría cifras de un dígito. Una expectativa calculada sobre
ocho operaciones es ruido con decimales.

**Jerarquía con retroceso.** Se intenta la casilla más específica; si no llega
al mínimo, se retrocede al nivel superior. El nivel usado queda registrado.

    estrategia + activo + dirección   (el ideal)
    estrategia + activo               (retroceso 1)
    estrategia + familia de activos   (retroceso 2)
    estrategia                        (retroceso 3)
    ninguno                           -> no se puntúa

**Encogimiento hacia la media.** La estadística de la casilla se mezcla con la
media global con peso que depende del tamaño de muestra. Con pocas operaciones
la puntuación tiende a la media; con muchas, a la casilla.

QUÉ SE ENCOGE Y QUÉ NO
-----------------------
Sólo los estadísticos POR OPERACIÓN: expectativa, profit factor, Sharpe y
payoff. La caída máxima NO se encoge: no es la media de nada, es el máximo de
una trayectoria.

POR QUÉ LA EXPECTATIVA Y NO EL PORCENTAJE DE ACIERTOS
-------------------------------------------------------
Tal como pedía la especificación, y es correcto: una estrategia que acierta el
40% de las veces pero gana el triple de lo que pierde es excelente, y una que
acierta el 70% pero pierde cuatro veces lo que gana es ruinosa. El porcentaje
de aciertos por sí solo es la métrica que más dinero ha destruido en este
oficio.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from qq_core.domain.signal import Direction, Signal

UMBRAL_STRONG = 80.0
UMBRAL_MODERATE = 65.0
UMBRAL_WEAK = 50.0

K_ENCOGIMIENTO = 50.0
"""Constante de encogimiento.

Con 50 operaciones, la estimación de la casilla pesa la mitad. Es un valor
deliberadamente exigente: obliga a tener bastante muestra antes de que una
casilla concreta domine su propia puntuación.
"""


class QualityClass(StrEnum):
    """Clasificación de la señal según su puntuación."""

    STRONG = "STRONG"
    MODERATE = "MODERATE"
    WEAK = "WEAK"
    REJECTED = "REJECTED"

    @property
    def is_actionable(self) -> bool:
        """Sólo STRONG y MODERATE llegan al motor de dimensionamiento."""
        return self in (QualityClass.STRONG, QualityClass.MODERATE)


class ScoringLevel(StrEnum):
    """Nivel de la jerarquía sobre el que se pudo puntuar."""

    STRATEGY_SYMBOL_DIRECTION = "estrategia+activo+direccion"
    STRATEGY_SYMBOL = "estrategia+activo"
    STRATEGY_FAMILY = "estrategia+familia"
    STRATEGY = "estrategia"
    NONE = "sin_muestra"


@dataclass(frozen=True)
class TradeStats:
    """Estadísticas de un conjunto de operaciones.

    Attributes:
        n_trades: Número de operaciones.
        win_rate: Proporción de operaciones ganadoras.
        avg_win: Ganancia media de las ganadoras, en tanto por uno.
        avg_loss: Pérdida media de las perdedoras, en positivo.
        expectancy: Resultado esperado por operación.
        profit_factor: Suma de ganancias sobre suma de pérdidas.
        payoff_ratio: Ganancia media sobre pérdida media.
        sharpe_annual: Sharpe anualizado de la serie.
        max_drawdown: Caída máxima, en positivo.
    """

    n_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    expectancy: float
    profit_factor: float
    payoff_ratio: float
    sharpe_annual: float
    max_drawdown: float

    @classmethod
    def from_trade_returns(
        cls, returns: pd.Series | np.ndarray, sharpe_annual: float = 0.0
    ) -> TradeStats:
        """Calcula estadísticas a partir de los resultados por operación."""
        r = np.asarray(pd.Series(returns).dropna(), dtype=float)
        n = int(r.size)
        if n == 0:
            return cls(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, sharpe_annual, 0.0)

        ganadoras = r[r > 0]
        perdedoras = r[r < 0]

        win_rate = float(ganadoras.size) / n
        avg_win = float(ganadoras.mean()) if ganadoras.size else 0.0
        avg_loss = float(-perdedoras.mean()) if perdedoras.size else 0.0

        # Expectativa = (P_ganar x GananciaMedia) - (P_perder x PerdidaMedia)
        expectativa = win_rate * avg_win - (1.0 - win_rate) * avg_loss

        suma_g = float(ganadoras.sum()) if ganadoras.size else 0.0
        suma_p = float(-perdedoras.sum()) if perdedoras.size else 0.0
        pf = suma_g / suma_p if suma_p > 1e-12 else (suma_g / 1e-12 if suma_g else 0.0)
        pf = min(pf, 100.0)

        payoff = avg_win / avg_loss if avg_loss > 1e-12 else 0.0

        curva = np.cumprod(1.0 + r)
        pico = np.maximum.accumulate(curva)
        dd = float(np.max((pico - curva) / pico)) if curva.size else 0.0

        return cls(
            n_trades=n,
            win_rate=win_rate,
            avg_win=avg_win,
            avg_loss=avg_loss,
            expectancy=float(expectativa),
            profit_factor=float(pf),
            payoff_ratio=float(payoff),
            sharpe_annual=float(sharpe_annual),
            max_drawdown=dd,
        )


@dataclass(frozen=True)
class QualityScore:
    """Puntuación de calidad de una señal.

    Attributes:
        score: Puntuación de 0 a 100.
        classification: Clasificación derivada del umbral.
        level: Nivel de la jerarquía sobre el que se pudo puntuar.
        n_trades: Operaciones de la muestra usada.
        stats: Estadísticas de esa muestra, ya encogidas.
        validation_verdict: Veredicto del Módulo 05 para la combinación.
        shrinkage_weight: Peso dado a la casilla frente a la media global.
        reasons: Códigos que explican la puntuación.
        explanation: Explicación en lenguaje llano.
    """

    score: float
    classification: QualityClass
    level: ScoringLevel
    n_trades: int
    stats: TradeStats | None
    validation_verdict: object | None
    shrinkage_weight: float
    reasons: tuple[str, ...]
    explanation: str

    def to_row(self) -> dict[str, Any]:
        return {
            "Puntuación": round(self.score, 1),
            "Clasificación": self.classification.value,
            "Nivel de muestra": self.level.value,
            "Operaciones": self.n_trades,
            "Validación": (
                self.validation_verdict.value if self.validation_verdict else "sin_datos"
            ),
            "Peso de la casilla": round(self.shrinkage_weight, 2),
            "Motivos": ", ".join(self.reasons),
        }


def _normalizar(valor: float, malo: float, bueno: float) -> float:
    """Lleva un valor a la escala 0-100 acotando en los extremos."""
    if bueno == malo:
        return 50.0
    x = (valor - malo) / (bueno - malo)
    return float(min(max(x, 0.0), 1.0) * 100.0)


def _encoger(propia: float, global_: float, n: int) -> tuple[float, float]:
    """Mezcla la estimación de la casilla con la media global.

    El peso de la casilla crece con el número de operaciones. Con muestra
    pequeña la puntuación tiende a la media global, que es la respuesta honesta
    cuando no se sabe nada específico.

    Returns:
        Tupla (valor encogido, peso dado a la casilla).
    """
    peso = n / (n + K_ENCOGIMIENTO)
    return propia * peso + global_ * (1.0 - peso), peso


def score_signal(
    signal: Signal,
    stats_by_bucket: dict[tuple, TradeStats],
    global_stats: TradeStats,
    symbol_family: dict[str, str] | None = None,
    min_trades: int = 30,
) -> QualityScore:
    """Puntúa una señal de 0 a 100.

    Args:
        signal: Señal a puntuar.
        stats_by_bucket: Estadísticas por casilla. Las claves aceptadas son
            `(estrategia, símbolo, dirección)`, `(estrategia, símbolo)`,
            `(estrategia, familia)` y `(estrategia,)`.
        global_stats: Estadísticas del conjunto, para el encogimiento.
        symbol_family: Familia de cada símbolo (por ejemplo índices, metales,
            energía, divisas). Permite el retroceso de nivel 2.
        min_trades: Operaciones mínimas para usar una casilla.

    Returns:
        Puntuación con su explicación.
    """
    motivos: list[str] = []


    # --- Barrera única: muestra suficiente, con retroceso de nivel ---
    familia = (symbol_family or {}).get(signal.symbol, "otros")
    candidatos: list[tuple[tuple, ScoringLevel]] = [
        (
            (signal.strategy, signal.symbol, signal.direction.value),
            ScoringLevel.STRATEGY_SYMBOL_DIRECTION,
        ),
        ((signal.strategy, signal.symbol), ScoringLevel.STRATEGY_SYMBOL),
        ((signal.strategy, familia), ScoringLevel.STRATEGY_FAMILY),
        ((signal.strategy,), ScoringLevel.STRATEGY),
    ]

    elegida: TradeStats | None = None
    nivel = ScoringLevel.NONE

    for clave, lvl in candidatos:
        st = stats_by_bucket.get(clave)
        if st is not None and st.n_trades >= min_trades:
            elegida, nivel = st, lvl
            break

    if elegida is None:
        return QualityScore(
            score=0.0,
            classification=QualityClass.REJECTED,
            level=ScoringLevel.NONE,
            n_trades=0,
            stats=None,
            validation_verdict=None,
            shrinkage_weight=0.0,
            reasons=("INSUFFICIENT_SAMPLE",),
            explanation=(
                f"No hay ninguna agrupación con al menos {min_trades} "
                f"operaciones sobre la que estimar la expectativa. Puntuar con "
                f"menos sería inventarse una precisión que la muestra no tiene."
            ),
        )

    if nivel is not ScoringLevel.STRATEGY_SYMBOL_DIRECTION:
        motivos.append("FALLBACK_" + nivel.name)

    # --- Puntuación por componentes, todas encogidas hacia la media ---
    n = elegida.n_trades

    # Sólo se encogen los estadísticos POR OPERACIÓN. La caída máxima no se
    # encoge: no es la media de nada, es el máximo de una trayectoria. Mezclar
    # una caída propia del 21% con una media global del 97% no produce una
    # estimación más estable, produce un número sin sentido que arrastra la
    # puntuación a cero. Detectado por CA-74.
    exp_enc, peso = _encoger(elegida.expectancy, global_stats.expectancy, n)
    pf_enc, _ = _encoger(elegida.profit_factor, global_stats.profit_factor, n)
    sr_enc, _ = _encoger(elegida.sharpe_annual, global_stats.sharpe_annual, n)
    payoff_enc, _ = _encoger(elegida.payoff_ratio, global_stats.payoff_ratio, n)
    dd_enc = elegida.max_drawdown

    componentes = {
        # La expectativa pesa más que nada: es la única que responde
        # directamente a "¿gano dinero por operación?".
        "expectativa": (_normalizar(exp_enc, -0.005, 0.015), 0.30),
        "profit_factor": (_normalizar(pf_enc, 0.8, 2.0), 0.20),
        "sharpe": (_normalizar(sr_enc, 0.0, 1.5), 0.20),
        "payoff": (_normalizar(payoff_enc, 0.7, 2.5), 0.15),
        "caida_maxima": (_normalizar(-dd_enc, -0.40, -0.05), 0.15),
    }

    puntuacion = sum(v * w for v, w in componentes.values())

    # Penalización por muestra escasa. Aunque el encogimiento ya modera la
    # estimación, una casilla justo en el mínimo no debe poder alcanzar STRONG.
    if peso < 0.5:
        puntuacion *= 0.85
        motivos.append("SMALL_SAMPLE_PENALTY")

    if elegida.expectancy <= 0:
        motivos.append("NEGATIVE_EXPECTANCY")

    if puntuacion >= UMBRAL_STRONG:
        clase = QualityClass.STRONG
    elif puntuacion >= UMBRAL_MODERATE:
        clase = QualityClass.MODERATE
    elif puntuacion >= UMBRAL_WEAK:
        clase = QualityClass.WEAK
    else:
        clase = QualityClass.REJECTED

    explicacion = (
        f"Puntuación {puntuacion:.0f}/100 sobre {n} operaciones agrupadas por "
        f"{nivel.value}. Expectativa {exp_enc * 100:.2f}% por operación, "
        f"profit factor {pf_enc:.2f}, Sharpe {sr_enc:.2f}. "
        f"La casilla pesa un {peso * 100:.0f}% frente a la media global; el "
        f"resto es encogimiento por tamaño de muestra."
    )

    return QualityScore(
        score=float(puntuacion),
        classification=clase,
        level=nivel,
        n_trades=n,
        stats=elegida,
        validation_verdict=None,
        shrinkage_weight=float(peso),
        reasons=tuple(motivos),
        explanation=explicacion,
    )
