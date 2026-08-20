"""Prueba de Superioridad Predictiva (SPA) de Hansen con bootstrap por bloques.

QUÉ PREGUNTA RESPONDE
----------------------
"De las 207 combinaciones evaluadas, ¿la mejor supera de verdad a no hacer
nada, TENIENDO EN CUENTA que la elegimos precisamente por ser la mejor?"

Esa coletilla final es todo el problema. Preguntar por la mejor de 207 sin
corregir es como celebrar que el ganador de una lotería acertó los números.

POR QUÉ ESTA PRUEBA Y NO UNA CORRECCIÓN CLÁSICA
------------------------------------------------
Bonferroni y Benjamini-Hochberg necesitan saber (o suponer) cómo se relacionan
las pruebas entre sí. Nuestras 207 series están fuertemente correlacionadas:
trece índices bursátiles se mueven casi juntos y varias estrategias comparten
lógica de fondo. Suponer independencia daría un resultado tan conservador que
descartaría todo, incluido lo que funcione.

El bootstrap resuelve esto sin modelar nada: remuestrea las 207 series a la
vez, en bloques, conservando tanto la correlación entre ellas como la
dependencia temporal dentro de cada una. La distribución nula que produce ya
incorpora la estructura real de los datos.

POR QUÉ BLOQUES Y NO OBSERVACIONES SUELTAS
-------------------------------------------
Los rendimientos financieros no son independientes en el tiempo: la
volatilidad se agrupa, las tendencias persisten. Remuestrear días sueltos
destruiría esa estructura y produciría una distribución nula demasiado
estrecha, lo que haría la prueba demasiado permisiva. El bootstrap
estacionario de Politis y Romano remuestrea bloques de longitud aleatoria, que
preserva la dependencia local.

SPA FRENTE A REALITY CHECK
---------------------------
El Reality Check de White es la versión anterior de esta idea. Su problema es
que candidatos muy malos desplazan la distribución nula y hacen la prueba
excesivamente conservadora: basta con añadir estrategias basura a la búsqueda
para que sea más fácil pasar. El SPA de Hansen corrige eso descartando del
recentrado los candidatos claramente inferiores. Por eso se implementa el SPA.

REFERENCIAS
-----------
Politis, D. y Romano, J. (1994), "The Stationary Bootstrap".
White, H. (2000), "A Reality Check for Data Snooping".
Hansen, P. (2005), "A Test for Superior Predictive Ability".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

MIN_OBSERVACIONES = 60
"""Mínimo de observaciones para que el bootstrap por bloques tenga sentido."""


@dataclass(frozen=True)
class SPAResult:
    """Resultado de la prueba SPA.

    Attributes:
        p_value: Probabilidad de observar un resultado tan bueno como el mejor
            candidato si NINGUNO tuviera capacidad real. Valores bajos son
            evidencia a favor; por encima de 0.05 no hay evidencia.
        best_index: Posición del mejor candidato en la matriz de entrada.
        best_mean: Rendimiento medio por periodo del mejor candidato.
        statistic: Estadístico observado de la prueba.
        n_candidates: Número de candidatos evaluados conjuntamente.
        n_observations: Longitud de la serie.
        n_bootstrap: Réplicas de bootstrap ejecutadas.
        block_length: Longitud media de bloque usada.
    """

    p_value: float
    best_index: int
    best_mean: float
    statistic: float
    n_candidates: int
    n_observations: int
    n_bootstrap: int
    block_length: float

    @property
    def is_significant(self) -> bool:
        """Cierto si hay evidencia al 5% de que el mejor candidato es real."""
        return self.p_value < 0.05

    @property
    def explanation(self) -> str:
        """Lectura en lenguaje llano del resultado."""
        if self.is_significant:
            return (
                f"El mejor de {self.n_candidates} candidatos supera al "
                f"benchmark incluso descontando que fue elegido por ser el "
                f"mejor (p = {self.p_value:.4f}). Hay evidencia estadística."
            )
        return (
            f"El mejor de {self.n_candidates} candidatos NO supera al "
            f"benchmark una vez descontado el efecto de haber buscado entre "
            f"tantos (p = {self.p_value:.4f}). Un resultado así es compatible "
            f"con que ninguna de las combinaciones tenga capacidad real."
        )


def optimal_block_length(n_observations: int) -> float:
    """Longitud media de bloque en función del tamaño de muestra.

    Regla práctica n^(1/3), habitual en la literatura de bootstrap por bloques.
    No es óptima en sentido estricto —la longitud óptima depende de la
    autocorrelación real de la serie— pero es robusta y evita introducir un
    parámetro más que ajustar, que es precisamente lo que este módulo combate.
    """
    return max(2.0, float(n_observations) ** (1.0 / 3.0))


def stationary_bootstrap_indices(
    n_observations: int, block_length: float, rng: np.random.Generator
) -> np.ndarray:
    """Genera índices para una réplica del bootstrap estacionario.

    Recorre la serie eligiendo un punto de partida al azar y avanzando de forma
    contigua; en cada paso, con probabilidad 1/block_length, salta a otro punto
    aleatorio. Esto produce bloques de longitud geométrica con media
    `block_length`, envolviendo la serie por el final.

    Args:
        n_observations: Longitud de la serie original.
        block_length: Longitud media de bloque deseada.
        rng: Generador aleatorio, para reproducibilidad.

    Returns:
        Vector de índices de longitud `n_observations`.
    """
    p_salto = 1.0 / block_length
    indices = np.empty(n_observations, dtype=np.int64)

    saltos = rng.random(n_observations) < p_salto
    nuevos = rng.integers(0, n_observations, size=n_observations)

    actual = int(nuevos[0])
    for i in range(n_observations):
        if i > 0:
            actual = int(nuevos[i]) if saltos[i] else (actual + 1) % n_observations
        indices[i] = actual

    return indices


def _hac_variance(serie: np.ndarray, block_length: float) -> float:
    """Varianza de la media con corrección por autocorrelación.

    Estimador de Newey-West con núcleo de Bartlett. Necesario porque los
    rendimientos están autocorrelacionados y la varianza clásica de la media
    los subestimaría, haciendo la prueba demasiado permisiva.
    """
    n = serie.size
    if n < 2:
        return 0.0

    centrada = serie - serie.mean()
    gamma0 = float((centrada**2).mean())
    total = gamma0

    max_rezago = min(int(block_length * 2), n - 1)
    for k in range(1, max_rezago + 1):
        gamma_k = float((centrada[k:] * centrada[:-k]).mean())
        peso = 1.0 - k / (max_rezago + 1.0)
        total += 2.0 * peso * gamma_k

    return max(total, 1e-16)


def superior_predictive_ability(
    performance: np.ndarray,
    benchmark: np.ndarray | None = None,
    n_bootstrap: int = 2000,
    seed: int = 20260818,
) -> SPAResult:
    """Ejecuta la prueba SPA de Hansen sobre un conjunto de candidatos.

    Args:
        performance: Matriz de forma (n_observaciones, n_candidatos) con los
            rendimientos por periodo de cada combinación estrategia-instrumento.
            Todas las columnas deben cubrir el MISMO rango de fechas; si no,
            la correlación entre ellas queda mal estimada.
        benchmark: Serie de referencia de longitud n_observaciones. Si es
            `None`, se usa cero, que representa "no hacer nada" — la referencia
            correcta para un sistema que puede elegir no operar.
        n_bootstrap: Réplicas de bootstrap. Dos mil da un error de Monte Carlo
            de aproximadamente 0.005 en el p-valor, suficiente para decidir
            contra un umbral de 0.05.
        seed: Semilla, para que el resultado sea reproducible y auditable.

    Returns:
        Resultado de la prueba.

    Raises:
        ValueError: Si la matriz está vacía o es demasiado corta.
    """
    perf = np.asarray(performance, dtype=float)
    if perf.ndim == 1:
        perf = perf.reshape(-1, 1)

    n_obs, n_cand = perf.shape
    if n_obs < MIN_OBSERVACIONES:
        raise ValueError(
            f"se requieren al menos {MIN_OBSERVACIONES} observaciones, hay {n_obs}"
        )
    if n_cand < 1:
        raise ValueError("no hay candidatos que evaluar")

    base = np.zeros(n_obs) if benchmark is None else np.asarray(benchmark, dtype=float)
    if base.size != n_obs:
        raise ValueError("el benchmark no tiene la misma longitud que las series")

    # d[t,k] = exceso del candidato k sobre el benchmark en el periodo t.
    d = perf - base.reshape(-1, 1)
    d_medio = d.mean(axis=0)

    bloque = optimal_block_length(n_obs)
    omega = np.sqrt(
        np.array([_hac_variance(d[:, k], bloque) for k in range(n_cand)]) / n_obs
    )
    omega = np.maximum(omega, 1e-12)

    # Estadístico observado: el mejor t-ratio, truncado en cero.
    t_ratios = d_medio / omega
    estadistico = float(max(t_ratios.max(), 0.0))
    mejor = int(np.argmax(t_ratios))

    # Recentrado de Hansen. Los candidatos claramente inferiores se recentran
    # a cero para que no desplacen la distribución nula. Este es exactamente el
    # punto en que el SPA mejora al Reality Check de White: sin él, añadir
    # estrategias malas a la búsqueda facilitaría pasar la prueba.
    umbral = -omega * math.sqrt(2.0 * math.log(math.log(max(n_obs, 3))))
    g = np.where(d_medio >= umbral, d_medio, 0.0)

    rng = np.random.default_rng(seed)
    superan = 0

    for _ in range(n_bootstrap):
        idx = stationary_bootstrap_indices(n_obs, bloque, rng)
        d_boot = d[idx, :].mean(axis=0)
        t_boot = (d_boot - g) / omega
        if float(max(t_boot.max(), 0.0)) >= estadistico:
            superan += 1

    # Corrección de continuidad: el p-valor nunca es exactamente cero, porque
    # con un número finito de réplicas no puede descartarse un valor extremo.
    p_valor = (superan + 1.0) / (n_bootstrap + 1.0)

    return SPAResult(
        p_value=float(p_valor),
        best_index=mejor,
        best_mean=float(d_medio[mejor]),
        statistic=estadistico,
        n_candidates=n_cand,
        n_observations=n_obs,
        n_bootstrap=n_bootstrap,
        block_length=bloque,
    )
