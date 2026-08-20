"""Probabilidad de sobreajuste (PBO) por validación cruzada combinatoria.

QUÉ MIDE Y POR QUÉ ES LA CIFRA MÁS ÚTIL PARA EXPLICAR
-------------------------------------------------------
Responde a una pregunta que cualquiera entiende sin saber estadística:

    "Cuando elijo la mejor combinación mirando una parte del histórico,
     ¿sigue siendo buena en la parte que no miré?"

Si la respuesta es "casi nunca", el proceso de selección no está encontrando
estrategias: está encontrando casualidades. La PBO pone número a ese "casi
nunca".

CÓMO FUNCIONA
-------------
1. Se parte el histórico en S trozos iguales.
2. Se forman TODAS las maneras de repartir esos trozos en dos mitades.
3. En cada reparto: se elige el mejor candidato en una mitad y se mira en qué
   puesto queda en la otra.
4. La PBO es la proporción de repartos en los que el campeón de una mitad cae
   por debajo de la mediana en la otra.

POR QUÉ COMBINATORIA Y NO UN SIMPLE CORTE EN DOS
--------------------------------------------------
Partir el histórico una sola vez —primera mitad para elegir, segunda para
comprobar— da UN resultado, que depende por completo de dónde se hizo el
corte. Si la segunda mitad cae en un mercado alcista, las estrategias
compradoras parecerán buenas por razones que nada tienen que ver con su
mérito.

La versión combinatoria genera muchos repartos distintos y produce una
distribución en lugar de una anécdota. Con S=10 trozos salen 252 repartos.

CÓMO SE LEE EL RESULTADO
-------------------------
    PBO < 0.20   El proceso de selección tiene valor.
    0.20 - 0.50  Zona dudosa. La selección aporta poco.
    PBO > 0.50   El proceso es peor que elegir al azar: el campeón dentro de
                 la muestra tiende a quedar en la mitad mala fuera de ella.

LIMITACIÓN IMPORTANTE DE ESTA MÉTRICA
--------------------------------------
La PBO NO llega a 0.5 sobre candidatos de puro ruido, aunque la intuición diga
que debería. El motivo es que las dos mitades salen de la MISMA muestra finita:
una serie que por azar tiene media global positiva tendrá media positiva en
ambas mitades, porque las medias de las dos mitades promedian a la global. Esa
"suerte compartida" hace que el campeón persista más de lo que persistiría con
muestras independientes.

Medido en `tests/test_validacion.py`: 40 series de ruido puro dan una PBO en
torno a 0.24, no 0.50.

CONSECUENCIA PRÁCTICA: la PBO por sí sola es la más débil de las tres pruebas
del módulo y NO debe usarse aislada. Una PBO baja no acredita nada si el
contraste conjunto (SPA) no encuentra evidencia. Por eso el veredicto de
`report.py` exige superar las tres barreras y coloca el SPA primero.

REFERENCIA
----------
Bailey, Borwein, López de Prado y Zhu (2017), "The Probability of Backtest
Overfitting".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np

MIN_CANDIDATOS = 2
"""Con un solo candidato no hay selección que evaluar."""


@dataclass(frozen=True)
class CSCVResult:
    """Resultado de la validación cruzada combinatoria.

    Attributes:
        pbo: Probabilidad de sobreajuste, entre 0 y 1.
        n_splits: Número de repartos evaluados.
        n_blocks: Trozos en que se dividió el histórico.
        n_candidates: Candidatos comparados.
        median_logit: Mediana del logit del rango relativo fuera de muestra.
            Positivo indica que el campeón tiende a seguir arriba.
        oos_ranks: Rango relativo fuera de muestra del campeón en cada reparto,
            entre 0 (el peor) y 1 (el mejor).
        degradation: Diferencia media entre el rendimiento del campeón dentro
            y fuera de muestra. Cuánto se desinfla al salir del periodo en que
            fue elegido.
    """

    pbo: float
    n_splits: int
    n_blocks: int
    n_candidates: int
    median_logit: float
    oos_ranks: tuple[float, ...]
    degradation: float

    @property
    def is_acceptable(self) -> bool:
        """Cierto si la probabilidad de sobreajuste está por debajo del 20%."""
        return self.pbo < 0.20

    @property
    def explanation(self) -> str:
        """Lectura en lenguaje llano del resultado."""
        pct = self.pbo * 100
        if self.pbo < 0.20:
            juicio = "El proceso de selección aporta valor."
        elif self.pbo < 0.50:
            juicio = "El proceso de selección aporta poco; zona dudosa."
        else:
            juicio = (
                "El proceso de selección es peor que elegir al azar: lo que "
                "parece mejor en una parte del histórico tiende a ser malo en "
                "el resto."
            )
        return (
            f"En el {pct:.1f}% de los repartos, la combinación elegida como "
            f"mejor en una mitad del histórico quedó por debajo de la mediana "
            f"en la otra mitad. {juicio}"
        )


def _sharpe_por_columna(bloque: np.ndarray) -> np.ndarray:
    """Sharpe por observación de cada columna. Cero si no hay variación."""
    media = bloque.mean(axis=0)
    desv = bloque.std(axis=0, ddof=1)
    return np.where(desv > 1e-12, media / np.where(desv > 1e-12, desv, 1.0), 0.0)


def probability_of_backtest_overfitting(
    performance: np.ndarray, n_blocks: int = 10
) -> CSCVResult:
    """Calcula la probabilidad de sobreajuste del proceso de selección.

    Args:
        performance: Matriz (n_observaciones, n_candidatos) de rendimientos por
            periodo. Todas las columnas deben cubrir el mismo rango de fechas.
        n_blocks: Trozos en que se divide el histórico. Debe ser par. Diez
            produce 252 repartos, que es un buen equilibrio entre precisión y
            tiempo de cálculo. Valores altos generan una explosión combinatoria.

    Returns:
        Resultado de la validación cruzada combinatoria.

    Raises:
        ValueError: Si hay menos de dos candidatos, `n_blocks` es impar, o la
            serie es demasiado corta para dividirse.
    """
    perf = np.asarray(performance, dtype=float)
    if perf.ndim != 2:
        raise ValueError("se espera una matriz (observaciones, candidatos)")

    n_obs, n_cand = perf.shape
    if n_cand < MIN_CANDIDATOS:
        raise ValueError(
            f"se requieren al menos {MIN_CANDIDATOS} candidatos, hay {n_cand}"
        )
    if n_blocks % 2 != 0:
        raise ValueError("n_blocks debe ser par para partir en dos mitades iguales")
    if n_obs < n_blocks * 10:
        raise ValueError(
            f"serie demasiado corta: {n_obs} observaciones para {n_blocks} trozos"
        )

    # Trozos contiguos. Contiguos y no aleatorios a propósito: mantener el
    # orden temporal dentro de cada trozo preserva la autocorrelación, y es lo
    # que hace que el resultado se parezca a lo que pasaría en la práctica.
    cortes = np.array_split(np.arange(n_obs), n_blocks)

    mitad = n_blocks // 2
    repartos = list(combinations(range(n_blocks), mitad))

    logits: list[float] = []
    rangos: list[float] = []
    degradaciones: list[float] = []

    for dentro in repartos:
        fuera = tuple(b for b in range(n_blocks) if b not in dentro)

        idx_dentro = np.concatenate([cortes[b] for b in dentro])
        idx_fuera = np.concatenate([cortes[b] for b in fuera])

        sr_dentro = _sharpe_por_columna(perf[idx_dentro, :])
        sr_fuera = _sharpe_por_columna(perf[idx_fuera, :])

        campeon = int(np.argmax(sr_dentro))
        degradaciones.append(float(sr_dentro[campeon] - sr_fuera[campeon]))

        # Rango relativo del campeón fuera de muestra, entre 0 y 1.
        orden = np.argsort(np.argsort(sr_fuera))
        rango = float(orden[campeon]) / max(n_cand - 1, 1)
        rangos.append(rango)

        # Se acota para que el logit no sea infinito en los extremos.
        w = min(max(rango, 1e-6), 1.0 - 1e-6)
        logits.append(math.log(w / (1.0 - w)))

    arr_logits = np.array(logits)
    pbo = float((arr_logits <= 0.0).mean())

    return CSCVResult(
        pbo=pbo,
        n_splits=len(repartos),
        n_blocks=n_blocks,
        n_candidates=n_cand,
        median_logit=float(np.median(arr_logits)),
        oos_ranks=tuple(rangos),
        degradation=float(np.mean(degradaciones)),
    )
