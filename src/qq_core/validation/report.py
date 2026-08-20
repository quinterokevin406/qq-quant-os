"""Informe combinado: los tres métodos y el veredicto por combinación.

POR QUÉ TRES MÉTODOS Y NO UNO
-------------------------------
Cada uno responde a una pregunta distinta y falla de forma distinta:

- El SPA responde por el CONJUNTO: ¿hay algo aquí, o todo es ruido?
- El DSR responde por CADA CANDIDATO: ¿este supera lo que saldría por azar?
- La PBO responde por el PROCESO: ¿elegir por backtest sirve de algo?

Si los tres coinciden, la conclusión es firme. Si discrepan, la discrepancia
misma es información: por ejemplo, un SPA significativo con una PBO alta
sugiere que hay señal en el conjunto pero que el criterio de selección no sabe
encontrarla.

Por eso el informe muestra los tres y no un semáforo único. Reducirlo a un
número escondería justamente la parte interesante.

REGLA DE VEREDICTO
------------------
Una combinación se marca como VALIDADA sólo si supera las tres barreras. Es
deliberadamente estricto. El coste de un falso positivo aquí es capital real
perdido; el coste de un falso negativo es una oportunidad no aprovechada. No
son simétricos.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from qq_core.validation.pbo import CSCVResult, probability_of_backtest_overfitting
from qq_core.validation.sharpe import (
    SharpeStats,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    sharpe_stats,
)
from qq_core.validation.spa import SPAResult, superior_predictive_ability

UMBRAL_DSR = 0.95
"""Probabilidad mínima del Deflated Sharpe para considerar un resultado real."""

UMBRAL_PBO = 0.20
"""Probabilidad máxima de sobreajuste tolerada."""

UMBRAL_SPA = 0.05
"""Nivel de significación del contraste conjunto."""


class Verdict(StrEnum):
    """Resultado de la validación de una combinación."""

    VALIDATED = "validada"
    """Supera las tres barreras. Candidata a operar, con las reservas del caso."""

    FAILS_DEFLATED_SHARPE = "no_supera_deflated_sharpe"
    """Su Sharpe es indistinguible del que saldría por azar al buscar tanto."""

    FAILS_OVERFITTING = "sobreajuste_probable"
    """El proceso de selección no generaliza fuera de muestra."""

    FAILS_JOINT_TEST = "sin_evidencia_conjunta"
    """Ninguna combinación del conjunto supera el contraste global."""

    INSUFFICIENT_SAMPLE = "muestra_insuficiente"
    """No hay datos suficientes para pronunciarse. NO es un aprobado."""

    @property
    def is_tradeable(self) -> bool:
        """Sólo el veredicto VALIDATED habilita a considerar la combinación."""
        return self is Verdict.VALIDATED


@dataclass(frozen=True)
class CombinationVerdict:
    """Veredicto sobre una combinación estrategia-instrumento.

    Attributes:
        strategy: Estrategia evaluada.
        symbol: Instrumento.
        verdict: Resultado.
        sharpe_annual: Sharpe anualizado observado, sin corregir.
        deflated_sharpe: Probabilidad de que el Sharpe sea real tras descontar
            la búsqueda. `None` si la muestra es insuficiente.
        probabilistic_sharpe: Probabilidad de que el Sharpe supere cero, sin
            descontar la búsqueda. Se muestra para que se vea la diferencia.
        expected_max_by_chance: Sharpe anualizado que cabría esperar por puro
            azar al buscar entre tantos candidatos. Es el listón a batir.
        n_observations: Tamaño de la muestra.
        reason: Explicación en lenguaje llano.
    """

    strategy: str
    symbol: str
    verdict: Verdict
    sharpe_annual: float
    deflated_sharpe: float | None
    probabilistic_sharpe: float | None
    expected_max_by_chance: float
    n_observations: int
    reason: str

    def to_row(self) -> dict[str, Any]:
        """Fila para tablas del panel."""
        return {
            "Estrategia": self.strategy,
            "Instrumento": self.symbol,
            "Veredicto": self.verdict.value,
            "Sharpe observado": round(self.sharpe_annual, 3),
            "Listón por azar": round(self.expected_max_by_chance, 3),
            "Deflated Sharpe": (
                round(self.deflated_sharpe, 4)
                if self.deflated_sharpe is not None
                else None
            ),
            "Prob. Sharpe > 0": (
                round(self.probabilistic_sharpe, 4)
                if self.probabilistic_sharpe is not None
                else None
            ),
            "Observaciones": self.n_observations,
            "Motivo": self.reason,
        }


@dataclass(frozen=True)
class ValidationReport:
    """Informe completo de validación del conjunto de candidatos.

    Attributes:
        generated_at: Momento de generación.
        n_trials_used: Número de ensayos usado en las correcciones.
        trials_is_lower_bound: Si ese número es una cota inferior.
        spa: Resultado del contraste conjunto.
        cscv: Resultado de la validación cruzada combinatoria.
        verdicts: Veredicto de cada combinación.
        risk_free_annual: Tipo sin riesgo usado.
    """

    generated_at: datetime
    n_trials_used: int
    trials_is_lower_bound: bool
    spa: SPAResult | None
    cscv: CSCVResult | None
    verdicts: tuple[CombinationVerdict, ...]
    risk_free_annual: float
    benchmark_is_buy_and_hold: bool = False
    """Si el contraste se hizo contra comprar y mantener cada instrumento."""

    @property
    def n_validated(self) -> int:
        return sum(1 for v in self.verdicts if v.verdict.is_tradeable)

    @property
    def headline(self) -> str:
        """Conclusión en una frase, apta para enseñar a un no técnico."""
        total = len(self.verdicts)
        if self.n_validated == 0:
            return (
                f"De {total} combinaciones evaluadas, NINGUNA supera la "
                f"corrección estadística por selección múltiple. El sistema "
                f"no tiene todavía evidencia para recomendar operar con "
                f"capital real."
            )
        return (
            f"De {total} combinaciones evaluadas, {self.n_validated} superan "
            f"las tres pruebas de validación. Las restantes son compatibles "
            f"con el azar."
        )

    def to_frame(self) -> pd.DataFrame:
        """Tabla de veredictos ordenada por Sharpe descendente."""
        if not self.verdicts:
            return pd.DataFrame()
        df = pd.DataFrame([v.to_row() for v in self.verdicts])
        return df.sort_values("Sharpe observado", ascending=False)

    def caveats(self) -> list[str]:
        """Limitaciones que deben acompañar siempre a este informe.

        No es un adorno legal. Cada una de estas frases describe una razón
        concreta por la que el informe podría estar equivocado, y todas siguen
        siendo ciertas mientras no se resuelvan.
        """
        avisos = []
        if self.trials_is_lower_bound:
            avisos.append(
                "El número de ensayos usado es una cota inferior: las "
                "configuraciones probadas antes de existir el registro no "
                "están contadas. Las correcciones son por tanto OPTIMISTAS."
            )
        if self.risk_free_annual == 0.0:
            avisos.append(
                "El tipo sin riesgo se ha fijado en cero. Con tipos positivos "
                "esto infla todos los Sharpe. Debe fijarse al valor real."
            )
        avisos.append(
            "Sólo el coste de financiación de US500 está medido en el "
            "terminal. Los otros 22 instrumentos usan una estimación del -9% "
            "anual. Si el valor real difiere, el orden de las combinaciones "
            "cambia y este informe habría que rehacerlo."
        )
        if not self.benchmark_is_buy_and_hold:
            avisos.append(
                "El contraste se hizo contra NO OPERAR, no contra comprar y "
                "mantener. En un periodo alcista, cualquier estrategia sesgada "
                "a comprar supera ese listón sin mérito propio: captura la "
                "subida del mercado en lugar de encontrar ineficiencias."
            )
        avisos.append(
            "Superar la validación es condición NECESARIA, no suficiente. "
            "Significa que el resultado no es atribuible al azar de la "
            "búsqueda; no significa que vaya a repetirse en el futuro."
        )
        return avisos


def validate_candidates(
    returns_by_combination: dict[tuple[str, str], pd.Series],
    n_trials: int | None = None,
    risk_free_annual: float = 0.0,
    n_bootstrap: int = 2000,
    n_blocks: int = 10,
    seed: int = 20260818,
    benchmark_by_symbol: dict[str, pd.Series] | None = None,
) -> ValidationReport:
    """Valida un conjunto de combinaciones con los tres métodos.

    Args:
        returns_by_combination: Diccionario con clave (estrategia, instrumento)
            y valor la serie de rendimientos por periodo del backtest.
        n_trials: Número de ensayos a usar en las correcciones. Si es `None`,
            se usa el número de combinaciones presentes, que es una cota
            inferior muy baja del número real.
        risk_free_annual: Tipo sin riesgo anual en tanto por uno.
        n_bootstrap: Réplicas del bootstrap del SPA.
        n_blocks: Trozos para la validación cruzada combinatoria.
        seed: Semilla, para reproducibilidad.
        benchmark_by_symbol: Rendimientos de comprar y mantener cada
            instrumento. Si se indica, cada combinación se contrasta contra
            SU PROPIO instrumento en lugar de contra cero.

            POR QUÉ IMPORTA. Contrastar contra cero pregunta "¿gana dinero?".
            En un mercado que subió durante diez años, cualquier estrategia
            sesgada a comprar responde que sí sin ningún mérito propio: está
            capturando la subida, no encontrando ineficiencias.

            Contrastar contra comprar y mantener pregunta "¿gana MÁS que
            quedarse quieto?", que es la única pregunta que justifica el coste
            y el riesgo de operar.

            Medido en la v1.10: con benchmark cero, el p-valor del contraste
            conjunto salía saturado en 0.0010 —el mínimo posible con 1.000
            réplicas— en las cuatro ejecuciones realizadas. Un estadístico que
            siempre satura no está midiendo nada.

    Returns:
        Informe completo.
    """
    combos = {k: pd.Series(v).dropna() for k, v in returns_by_combination.items()}
    combos = {k: v for k, v in combos.items() if len(v) > 0}

    if not combos:
        return ValidationReport(
            generated_at=datetime.now(timezone.utc),
            n_trials_used=0,
            trials_is_lower_bound=True,
            spa=None,
            cscv=None,
            verdicts=(),
            risk_free_annual=risk_free_annual,
        )

    claves = list(combos.keys())
    ensayos = n_trials if n_trials is not None else len(claves)

    # Alineación por fechas. Es obligatorio para el SPA y la PBO: comparar
    # series que cubren rangos distintos estimaría mal la correlación entre
    # ellas, que es justo lo que estas pruebas aprovechan.
    alineado = pd.DataFrame({f"{e}|{s}": combos[(e, s)] for e, s in claves}).dropna()

    estadisticos: dict[tuple[str, str], SharpeStats] = {
        k: sharpe_stats(combos[k], risk_free_annual=risk_free_annual) for k in claves
    }

    utilizables = [s.sharpe for s in estadisticos.values() if s.is_usable]
    varianza = float(np.var(utilizables, ddof=1)) if len(utilizables) > 1 else 0.0
    liston = expected_max_sharpe(ensayos, varianza)
    liston_anual = liston * np.sqrt(252)

    # --- Contraste conjunto y validación cruzada, sobre la matriz alineada ---
    spa: SPAResult | None = None
    cscv: CSCVResult | None = None
    matriz = alineado.to_numpy(dtype=float)

    # Benchmark por columna. Cada combinación se contrasta contra su propio
    # instrumento, alineado a las mismas fechas que la matriz de candidatos.
    base_alineada: np.ndarray | None = None
    if benchmark_by_symbol:
        columnas = []
        for estrategia, simbolo in claves:
            serie = benchmark_by_symbol.get(simbolo)
            columnas.append(
                pd.Series(0.0, index=alineado.index)
                if serie is None
                else pd.Series(serie).reindex(alineado.index).fillna(0.0)
            )
        base_alineada = pd.concat(columnas, axis=1).to_numpy(dtype=float)

    if matriz.shape[0] >= 60 and matriz.shape[1] >= 1:
        try:
            # El SPA admite un benchmark por serie: se resta antes de entrar.
            entrada = matriz if base_alineada is None else matriz - base_alineada
            spa = superior_predictive_ability(
                entrada, n_bootstrap=n_bootstrap, seed=seed
            )
        except ValueError:
            spa = None

    if matriz.shape[0] >= n_blocks * 10 and matriz.shape[1] >= 2:
        try:
            cscv = probability_of_backtest_overfitting(matriz, n_blocks=n_blocks)
        except ValueError:
            cscv = None

    hay_evidencia_conjunta = spa is not None and spa.p_value < UMBRAL_SPA
    proceso_generaliza = cscv is not None and cscv.pbo < UMBRAL_PBO

    # --- Veredicto por combinación ---
    veredictos: list[CombinationVerdict] = []

    for clave in claves:
        estrategia, simbolo = clave
        st = estadisticos[clave]

        if not st.is_usable:
            veredictos.append(
                CombinationVerdict(
                    strategy=estrategia,
                    symbol=simbolo,
                    verdict=Verdict.INSUFFICIENT_SAMPLE,
                    sharpe_annual=st.sharpe_annual,
                    deflated_sharpe=None,
                    probabilistic_sharpe=None,
                    expected_max_by_chance=float(liston_anual),
                    n_observations=st.n,
                    reason=(
                        f"Sólo {st.n} observaciones utilizables. No hay muestra "
                        f"suficiente para pronunciarse. Esto NO es un aprobado."
                    ),
                )
            )
            continue

        dsr = deflated_sharpe_ratio(st, ensayos, varianza)
        psr = probabilistic_sharpe_ratio(st, benchmark_sharpe=0.0)

        if not hay_evidencia_conjunta:
            veredicto = Verdict.FAILS_JOINT_TEST
            motivo = (
                f"El contraste conjunto sobre las {len(claves)} combinaciones "
                f"no encuentra evidencia (p = "
                f"{spa.p_value:.4f} ). Ningún candidato individual puede "
                f"validarse si el conjunto no supera esta barrera."
                if spa
                else "No se pudo ejecutar el contraste conjunto."
            )
        elif not proceso_generaliza:
            veredicto = Verdict.FAILS_OVERFITTING
            motivo = (
                f"Probabilidad de sobreajuste del "
                f"{cscv.pbo * 100:.1f}%, por encima del "
                f"{UMBRAL_PBO * 100:.0f}% admisible. Elegir por backtest no "
                f"generaliza fuera de muestra."
                if cscv
                else "No se pudo evaluar el sobreajuste."
            )
        elif dsr is None or dsr < UMBRAL_DSR:
            veredicto = Verdict.FAILS_DEFLATED_SHARPE
            valor = f"{dsr:.3f}" if dsr is not None else "no calculable"
            motivo = (
                f"Sharpe anualizado de {st.sharpe_annual:.2f} frente a un "
                f"listón por azar de {liston_anual:.2f}. Deflated Sharpe = "
                f"{valor}, por debajo de {UMBRAL_DSR}. Indistinguible del "
                f"ruido tras descontar {ensayos} ensayos."
            )
        else:
            veredicto = Verdict.VALIDATED
            motivo = (
                f"Supera las tres pruebas: contraste conjunto significativo, "
                f"sobreajuste del {cscv.pbo * 100:.1f}% y Deflated Sharpe de "
                f"{dsr:.3f} sobre un listón por azar de {liston_anual:.2f}."
            )

        veredictos.append(
            CombinationVerdict(
                strategy=estrategia,
                symbol=simbolo,
                verdict=veredicto,
                sharpe_annual=st.sharpe_annual,
                deflated_sharpe=dsr,
                probabilistic_sharpe=psr,
                expected_max_by_chance=float(liston_anual),
                n_observations=st.n,
                reason=motivo,
            )
        )

    return ValidationReport(
        generated_at=datetime.now(timezone.utc),
        n_trials_used=ensayos,
        trials_is_lower_bound=True,
        spa=spa,
        cscv=cscv,
        verdicts=tuple(veredictos),
        risk_free_annual=risk_free_annual,
        benchmark_is_buy_and_hold=bool(benchmark_by_symbol),
    )
