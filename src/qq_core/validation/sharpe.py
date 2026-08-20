"""Sharpe clásico, Probabilistic Sharpe Ratio y Deflated Sharpe Ratio.

POR QUÉ EL SHARPE DE `backtest.engine` NO SIRVE AQUÍ
-----------------------------------------------------
El motor de backtesting calcula `sharpe = cagr / volatilidad`. Es una variante
legítima y se conserva, pero NO es la que asume la matemática de este módulo.

Las fórmulas del Probabilistic Sharpe Ratio y del Deflated Sharpe Ratio están
derivadas sobre el Sharpe clásico —media de rendimientos dividida por su
desviación típica— y necesitan además la asimetría y la curtosis de la serie de
rendimientos. Pasarles un CAGR/volatilidad produce un número, no un error, y
ese número está mal.

Por eso este módulo recalcula el Sharpe desde la serie de rendimientos en lugar
de leer la métrica ya existente. La diferencia entre ambos es informativa: si
divergen mucho, la serie tiene rendimientos muy asimétricos.

REFERENCIAS
-----------
Bailey, D. y López de Prado, M. (2012), "The Sharpe Ratio Efficient Frontier".
Bailey, D. y López de Prado, M. (2014), "The Deflated Sharpe Ratio".
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

TRADING_DAYS = 252
"""Sesiones bursátiles por año. Debe coincidir con `backtest.engine`."""

EULER_MASCHERONI = 0.577_215_664_901_532_9
"""Constante de Euler-Mascheroni, necesaria para la esperanza del máximo."""

MIN_OBSERVACIONES = 30
"""Mínimo de rendimientos para que las fórmulas tengan sentido.

Por debajo de esto la estimación de curtosis es tan inestable que el resultado
del PSR es ruido. Se prefiere devolver `None` antes que un número falso.
"""


@dataclass(frozen=True)
class SharpeStats:
    """Estadísticos de una serie de rendimientos.

    Attributes:
        n: Número de observaciones.
        mean: Media por observación (NO anualizada).
        std: Desviación típica por observación (NO anualizada).
        sharpe: Sharpe por observación. Es el que usan PSR y DSR.
        sharpe_annual: Sharpe anualizado. Es el que se muestra al usuario.
        skew: Asimetría de la serie.
        kurtosis: Curtosis NO en exceso (una normal vale 3.0).
        risk_free_annual: Tipo sin riesgo anual usado para descontar.
    """

    n: int
    mean: float
    std: float
    sharpe: float
    sharpe_annual: float
    skew: float
    kurtosis: float
    risk_free_annual: float

    @property
    def is_usable(self) -> bool:
        """Indica si la muestra permite aplicar las correcciones."""
        return self.n >= MIN_OBSERVACIONES and self.std > 1e-12


def sharpe_stats(
    returns: pd.Series | np.ndarray,
    risk_free_annual: float = 0.0,
    periods_per_year: int = TRADING_DAYS,
) -> SharpeStats:
    """Calcula los estadísticos necesarios para PSR y DSR.

    Args:
        returns: Serie de rendimientos por periodo (no acumulados).
        risk_free_annual: Tipo sin riesgo anual en tanto por uno. Se descuenta
            de cada rendimiento antes de calcular el Sharpe.

            IMPORTANTE: el valor por defecto es 0.0 por compatibilidad con el
            motor de backtesting actual, pero cero NO es una elección neutra.
            Con tipos positivos, asumir cero infla todos los Sharpe. Debe
            fijarse explícitamente en la configuración del estudio.
        periods_per_year: Periodos por año para anualizar.

    Returns:
        Estadísticos de la serie. Si la muestra es insuficiente, `is_usable`
        será falso y los valores derivados serán cero.
    """
    r = np.asarray(pd.Series(returns).dropna(), dtype=float)
    n = int(r.size)

    if n < 2:
        return SharpeStats(n, 0.0, 0.0, 0.0, 0.0, 0.0, 3.0, risk_free_annual)

    rf_periodo = risk_free_annual / periods_per_year
    exceso = r - rf_periodo

    media = float(exceso.mean())
    desv = float(exceso.std(ddof=1))

    if desv <= 1e-12:
        return SharpeStats(n, media, 0.0, 0.0, 0.0, 0.0, 3.0, risk_free_annual)

    sr = media / desv
    centrado = exceso - media
    m2 = float((centrado**2).mean())
    asimetria = float((centrado**3).mean() / m2**1.5) if m2 > 1e-24 else 0.0
    curtosis = float((centrado**4).mean() / m2**2) if m2 > 1e-24 else 3.0

    return SharpeStats(
        n=n,
        mean=media,
        std=desv,
        sharpe=sr,
        sharpe_annual=sr * math.sqrt(periods_per_year),
        skew=asimetria,
        kurtosis=curtosis,
        risk_free_annual=risk_free_annual,
    )


def _phi(x: float) -> float:
    """Función de distribución acumulada de la normal estándar."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _phi_inv(p: float) -> float:
    """Inversa de la normal estándar por el método de Acklam.

    Se implementa a mano en lugar de depender de SciPy: el núcleo `qq_core`
    solo requiere numpy y pandas, y añadir SciPy por una única función sería
    una dependencia pesada para el despliegue en Streamlit Cloud.

    Precisión relativa mejor que 1.15e-9, suficiente de sobra aquí.
    """
    if not 0.0 < p < 1.0:
        raise ValueError(f"probabilidad fuera de (0,1): {p}")

    a = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02,
         1.383577518672690e02, -3.066479806614716e01, 2.506628277459239e00)
    b = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02,
         6.680131188771972e01, -1.328068155288572e01)
    c = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00,
         -2.549732539343734e00, 4.374664141464968e00, 2.938163982698783e00)
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00,
         3.754408661907416e00)

    p_bajo, p_alto = 0.02425, 1.0 - 0.02425

    if p < p_bajo:
        q = math.sqrt(-2.0 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )
    if p > p_alto:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1.0
        )

    q = p - 0.5
    r = q * q
    return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / (
        ((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1.0
    )


def probabilistic_sharpe_ratio(
    stats: SharpeStats, benchmark_sharpe: float = 0.0
) -> float | None:
    """Probabilidad de que el Sharpe real supere un umbral dado.

    Corrige el Sharpe observado por tres cosas que el Sharpe simple ignora:
    el tamaño de muestra, la asimetría y la curtosis. Una serie con muchas
    ganancias pequeñas y pérdidas raras pero enormes —perfil típico de vender
    volatilidad— tiene un Sharpe engañosamente alto, y el PSR lo penaliza.

    Args:
        stats: Estadísticos de la serie, de `sharpe_stats`.
        benchmark_sharpe: Umbral a superar, POR OBSERVACIÓN (no anualizado).
            Cero significa "¿es el Sharpe real mayor que cero?".

    Returns:
        Probabilidad entre 0 y 1, o `None` si la muestra es insuficiente.
        Convención habitual: se considera significativo por encima de 0.95.
    """
    if not stats.is_usable:
        return None

    sr = stats.sharpe
    denominador = 1.0 - stats.skew * sr + ((stats.kurtosis - 1.0) / 4.0) * sr**2

    if denominador <= 1e-12:
        # La combinación de asimetría y curtosis hace la varianza del
        # estimador no positiva. Ocurre con muestras patológicas; no se
        # inventa un resultado.
        return None

    z = (sr - benchmark_sharpe) * math.sqrt(stats.n - 1) / math.sqrt(denominador)
    return _phi(z)


def expected_max_sharpe(n_trials: int, variance_of_trials: float) -> float:
    """Sharpe máximo esperado al buscar entre `n_trials` candidatos sin skill.

    Este es el corazón del problema. Si se prueban 207 estrategias que NO
    tienen ninguna capacidad predictiva, la mejor de ellas no tendrá Sharpe
    cero: tendrá un Sharpe positivo y a menudo llamativo, sólo por azar. Esta
    función calcula cuánto vale ese "Sharpe gratis".

    Cualquier candidato que no supere este umbral es indistinguible del ruido.

    Args:
        n_trials: Número de candidatos evaluados. Debe salir del registro de
            ensayos, no de un recuento a ojo.
        variance_of_trials: Varianza de los Sharpe (por observación) entre los
            candidatos evaluados. Mide cuán dispersa fue la búsqueda.

    Returns:
        Sharpe esperado del mejor candidato bajo la hipótesis de que ninguno
        tiene capacidad real. Expresado POR OBSERVACIÓN.
    """
    if n_trials < 2 or variance_of_trials <= 0.0:
        return 0.0

    n = float(n_trials)
    sigma = math.sqrt(variance_of_trials)

    z1 = _phi_inv(1.0 - 1.0 / n)
    z2 = _phi_inv(1.0 - 1.0 / (n * math.e))

    return sigma * ((1.0 - EULER_MASCHERONI) * z1 + EULER_MASCHERONI * z2)


def deflated_sharpe_ratio(
    stats: SharpeStats, n_trials: int, variance_of_trials: float
) -> float | None:
    """Probabilidad de que el Sharpe sea real, descontando la búsqueda.

    Es el Probabilistic Sharpe Ratio evaluado no contra cero, sino contra el
    Sharpe que cabría esperar por azar tras buscar entre `n_trials`
    candidatos. Responde a la pregunta correcta: no "¿es este Sharpe mayor que
    cero?" sino "¿es mayor de lo que habría salido por casualidad al mirar 207
    combinaciones?".

    Args:
        stats: Estadísticos de la serie del candidato.
        n_trials: Número total de candidatos evaluados.
        variance_of_trials: Varianza de los Sharpe entre candidatos.

    Returns:
        Probabilidad entre 0 y 1, o `None` si la muestra es insuficiente.
        Por encima de 0.95 se considera que el resultado sobrevive.
    """
    umbral = expected_max_sharpe(n_trials, variance_of_trials)
    return probabilistic_sharpe_ratio(stats, benchmark_sharpe=umbral)
