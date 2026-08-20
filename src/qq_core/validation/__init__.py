"""Módulo 05 — Validación estadística y control de sobreajuste.

POR QUÉ EXISTE ESTE MÓDULO
---------------------------
El sistema evalúa 9 estrategias sobre 23 instrumentos: 207 combinaciones. Al
buscar entre 207 candidatos, algunos saldrán bien por puro azar. Ese es un
hecho aritmético, no una sospecha: con 207 series aleatorias sin ninguna
capacidad predictiva, la mejor de ellas tendrá un Sharpe aparentemente
excelente.

Presentar el mejor resultado de una búsqueda como si fuera el resultado de una
sola prueba es la forma más común de autoengaño en investigación cuantitativa.
Este módulo existe para impedirlo.

QUÉ RESPONDE Y QUÉ NO
----------------------
Responde: "¿este resultado sobrevive al hecho de que lo hemos elegido entre
muchos?"

NO responde: "¿esta estrategia ganará dinero?". Ninguna herramienta estadística
responde a eso. Superar la corrección es condición necesaria, nunca suficiente.

LOS CUATRO INSTRUMENTOS Y POR QUÉ ESTOS
----------------------------------------
1. `sharpe`   — Sharpe clásico, Probabilistic Sharpe Ratio y Deflated Sharpe
                Ratio. Corrigen un resultado individual por el número de
                ensayos, la asimetría, la curtosis y el tamaño de muestra.

2. `spa`      — Prueba de Superioridad Predictiva de Hansen con bootstrap por
                bloques. Responde a la pregunta global: "¿la MEJOR de las 207
                supera a no hacer nada, sabiendo que buscamos entre 207?".
                Es la prueba principal porque es la única que trata la
                correlación entre las series de forma nativa, sin suponerla.

3. `pbo`      — Probabilidad de sobreajuste por validación cruzada
                combinatoria. Mide si el mejor candidato dentro de la muestra
                sigue siéndolo fuera. Es la cifra más explicable a un no
                especialista.

4. `trials`   — Registro de ensayos. Sin él, el número de pruebas que se pasa
                a las correcciones es una estimación a la baja, y por tanto
                las correcciones resultan optimistas.

POR QUÉ NO BONFERRONI NI BENJAMINI-HOCHBERG
--------------------------------------------
Ambos suponen independencia (o dependencia positiva estructurada) entre las
pruebas. Nuestras 207 combinaciones no son independientes ni de lejos: trece
índices bursátiles se mueven casi al unísono, y varias estrategias comparten
lógica. Bonferroni sería tan conservador que descartaría incluso lo que
funcione; Benjamini-Hochberg no es válido bajo esta estructura de dependencia.
El bootstrap del SPA resuelve el problema remuestreando las series conjuntas,
que preserva la correlación observada sin necesidad de modelarla.

LIMITACIÓN CONOCIDA Y NO RESUELTA
----------------------------------
El recuento de ensayos que usan estas correcciones solo incluye lo que el
registro haya anotado. Las configuraciones que se probaron durante el
desarrollo del sistema, antes de existir este registro, no están contadas. El
número real de ensayos es mayor que el que conocemos, y por tanto TODAS las
correcciones de este módulo son optimistas. Está documentado en el informe que
genera `report.py` y debe seguir apareciendo ahí mientras siga siendo cierto.
"""

from __future__ import annotations

from qq_core.validation.pbo import CSCVResult, probability_of_backtest_overfitting
from qq_core.validation.purged import PurgedSplit, purged_kfold_splits
from qq_core.validation.report import (
    CombinationVerdict,
    ValidationReport,
    Verdict,
    validate_candidates,
)
from qq_core.validation.sharpe import (
    SharpeStats,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_stats,
)
from qq_core.validation.spa import SPAResult, stationary_bootstrap_indices, superior_predictive_ability
from qq_core.validation.trials import Trial, TrialRegistry

__all__ = [
    "CSCVResult",
    "CombinationVerdict",
    "PurgedSplit",
    "SPAResult",
    "SharpeStats",
    "Trial",
    "TrialRegistry",
    "ValidationReport",
    "Verdict",
    "deflated_sharpe_ratio",
    "expected_max_sharpe",
    "probabilistic_sharpe_ratio",
    "probability_of_backtest_overfitting",
    "purged_kfold_splits",
    "sharpe_stats",
    "stationary_bootstrap_indices",
    "superior_predictive_ability",
    "validate_candidates",
]
