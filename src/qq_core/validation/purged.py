"""Validación cruzada con purga y embargo.

EL PROBLEMA QUE RESUELVE
-------------------------
La validación cruzada normal reparte las observaciones al azar entre
entrenamiento y prueba. En series financieras eso es un error grave, por dos
motivos distintos:

**Solapamiento.** Una señal generada el lunes con horizonte de un mes usa
información que se sigue realizando durante las cuatro semanas siguientes. Si
el lunes cae en entrenamiento y el miércoles siguiente en prueba, la prueba
está contaminada: contiene el resultado de una decisión que el entrenamiento
ya conocía.

**Autocorrelación.** Los días contiguos se parecen. Un modelo puede "acertar"
en el día siguiente simplemente porque se parece al anterior, sin haber
aprendido nada.

Ambos hacen que el rendimiento fuera de muestra parezca mejor de lo que es.

LA SOLUCIÓN, EN DOS PARTES
---------------------------
**Purga.** Se eliminan del entrenamiento las observaciones cuyo periodo de
realización se solapa con el conjunto de prueba.

**Embargo.** Se eliminan además las observaciones inmediatamente posteriores
al conjunto de prueba. La purga cubre el solapamiento explícito; el embargo
cubre la autocorrelación residual que persiste después.

REFERENCIA
----------
López de Prado, M. (2018), "Advances in Financial Machine Learning", cap. 7.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PurgedSplit:
    """Un reparto entrenamiento/prueba con purga y embargo aplicados.

    Attributes:
        train: Índices de entrenamiento, ya purgados y con embargo.
        test: Índices de prueba.
        purged: Cuántas observaciones se eliminaron del entrenamiento.
        fold: Número de pliegue, empezando en cero.
    """

    train: np.ndarray
    test: np.ndarray
    purged: int
    fold: int

    @property
    def train_size(self) -> int:
        return int(self.train.size)

    @property
    def test_size(self) -> int:
        return int(self.test.size)


def purged_kfold_splits(
    n_observations: int,
    n_folds: int = 5,
    holding_periods: int = 1,
    embargo_pct: float = 0.01,
) -> list[PurgedSplit]:
    """Genera repartos de validación cruzada con purga y embargo.

    Args:
        n_observations: Longitud de la serie.
        n_folds: Número de pliegues. Cinco es el estándar; más pliegues dan
            conjuntos de prueba más pequeños y por tanto más ruidosos.
        holding_periods: Duración en periodos de la decisión más larga. Para
            estrategias con horizonte de un mes sobre datos diarios, unos 22.
            Determina cuánto hay que purgar a cada lado del conjunto de prueba.
        embargo_pct: Fracción de la serie a embargar tras el conjunto de
            prueba. El 1% es el valor habitual.

    Returns:
        Lista de repartos, en orden temporal.

    Raises:
        ValueError: Si los parámetros no permiten construir los pliegues.
    """
    if n_folds < 2:
        raise ValueError("se requieren al menos dos pliegues")
    if n_observations < n_folds * (holding_periods + 2):
        raise ValueError(
            f"serie demasiado corta ({n_observations}) para {n_folds} pliegues "
            f"con horizonte de {holding_periods} periodos"
        )

    indices = np.arange(n_observations)
    bloques = np.array_split(indices, n_folds)
    embargo = int(n_observations * embargo_pct)

    repartos: list[PurgedSplit] = []

    for k, test in enumerate(bloques):
        inicio, fin = int(test[0]), int(test[-1])

        # Purga a ambos lados: las observaciones cuyo periodo de realización
        # se solapa con el conjunto de prueba salen del entrenamiento.
        veto_inicio = inicio - holding_periods
        veto_fin = fin + holding_periods + embargo

        mascara = (indices < veto_inicio) | (indices > veto_fin)
        train = indices[mascara]

        # Cuántas se perdieron respecto a un k-fold sin purga.
        habrian_sido = n_observations - test.size
        repartos.append(
            PurgedSplit(
                train=train,
                test=np.asarray(test),
                purged=int(habrian_sido - train.size),
                fold=k,
            )
        )

    return repartos
