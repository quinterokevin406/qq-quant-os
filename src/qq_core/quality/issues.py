"""Modelo de problemas de calidad de datos (Módulo 02).

POR QUÉ ESTE MÓDULO ES EL MÁS IMPORTANTE Y EL MENOS VISTOSO
------------------------------------------------------------
Un error de datos no produce ningún fallo. Produce resultados creíbles y
falsos. Una serie con veinte días duplicados genera señales, backtests y
recomendaciones que parecen perfectamente razonables y que están mal.

El resto de la plataforma —indicadores, estrategias, riesgo, cartera— asume
que los datos son correctos. Este módulo es lo único que verifica esa
suposición.

PRINCIPIO: DETECTAR Y REPORTAR, NO CORREGIR EN SILENCIO
--------------------------------------------------------
La tentación es rellenar los huecos y suavizar los valores extraños. Se
rechaza deliberadamente: un dato corregido automáticamente es indistinguible
de un dato correcto, y eso destruye la trazabilidad.

Lo que se hace es marcar el problema, cuantificar su gravedad y decidir si la
serie sigue siendo apta para generar señales. Corregir, cuando proceda, es una
decisión humana registrada.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class Severity(StrEnum):
    """Gravedad de un problema de calidad."""

    INFO = "informativo"
    """Observación que no compromete el uso de la serie."""

    WARNING = "aviso"
    """Merma la fiabilidad. La serie sigue siendo utilizable con reservas."""

    CRITICAL = "crítico"
    """La serie no es apta para generar señales."""


class IssueType(StrEnum):
    """Tipos de problema que el módulo sabe detectar."""

    GAP = "hueco"
    """Faltan sesiones que deberían existir."""

    STALE = "serie congelada"
    """El precio se repite exactamente durante varias sesiones. Casi siempre
    indica que el proveedor dejó de actualizar, no que el mercado no se movió."""

    OUTLIER = "valor atípico"
    """Salto de precio incompatible con la volatilidad habitual del
    instrumento."""

    DUPLICATE = "fecha duplicada"
    """La misma sesión aparece más de una vez."""

    OHLC_INVALID = "barra incoherente"
    """Máximo por debajo del mínimo, o cierre fuera del rango."""

    NON_POSITIVE = "precio no positivo"
    """Precio cero o negativo. Siempre es un error del proveedor."""

    SHORT_HISTORY = "histórico insuficiente"
    """No hay barras suficientes para las estrategias configuradas."""

    STALE_FEED = "sin actualizar"
    """El último dato es demasiado antiguo. La serie ha dejado de recibir
    actualizaciones."""

    STEP_CHANGE = "salto de nivel"
    """Cambio brusco y permanente del nivel de precios, típico de un cambio de
    contrato o de una división de acciones no ajustada."""


@dataclass(frozen=True)
class QualityIssue:
    """Un problema detectado en una serie.

    Attributes:
        symbol: Instrumento afectado.
        issue_type: Naturaleza del problema.
        severity: Gravedad.
        count: Número de ocurrencias.
        detail: Explicación legible.
        dates: Fechas concretas afectadas, hasta un máximo razonable.
        metric: Valor numérico asociado, cuando aplica.
    """

    symbol: str
    issue_type: IssueType
    severity: Severity
    count: int
    detail: str
    dates: tuple[date, ...] = field(default_factory=tuple)
    metric: float | None = None

    def to_row(self) -> dict[str, Any]:
        return {
            "Instrumento": self.symbol,
            "Problema": self.issue_type.value,
            "Gravedad": self.severity.value,
            "Ocurrencias": self.count,
            "Detalle": self.detail,
            "Fechas": ", ".join(d.isoformat() for d in self.dates[:5])
            + ("…" if len(self.dates) > 5 else ""),
        }


@dataclass
class QualityReport:
    """Diagnóstico completo de una serie.

    Attributes:
        symbol: Instrumento.
        bars: Barras analizadas.
        first_date: Primera sesión disponible.
        last_date: Última sesión disponible.
        issues: Problemas encontrados.
        score: Puntuación de 0 a 100.
    """

    symbol: str
    bars: int
    first_date: date | None
    last_date: date | None
    issues: list[QualityIssue] = field(default_factory=list)
    score: float = 100.0

    @property
    def critical_issues(self) -> list[QualityIssue]:
        return [i for i in self.issues if i.severity is Severity.CRITICAL]

    @property
    def is_usable(self) -> bool:
        """Si la serie puede usarse para generar señales.

        Basta un problema crítico para descartarla. No se pondera ni se
        promedia: una serie con precios negativos no es «un poco» inutilizable.
        """
        return not self.critical_issues and self.score >= 60

    @property
    def status_label(self) -> str:
        if self.critical_issues:
            return "No utilizable"
        if self.score >= 90:
            return "Correcta"
        if self.score >= 75:
            return "Aceptable"
        if self.score >= 60:
            return "Con reservas"
        return "Deficiente"

    @property
    def status_color(self) -> str:
        """Color del semáforo, para el panel."""
        if self.critical_issues or self.score < 60:
            return "#e05252"
        if self.score >= 90:
            return "#2f9e79"
        return "#d9a441"

    def summary_row(self) -> dict[str, Any]:
        return {
            "Instrumento": self.symbol,
            "Estado": self.status_label,
            "Puntuación": round(self.score, 1),
            "Barras": self.bars,
            "Desde": self.first_date.isoformat() if self.first_date else "—",
            "Hasta": self.last_date.isoformat() if self.last_date else "—",
            "Problemas": len(self.issues),
            "Críticos": len(self.critical_issues),
            "Apta para señales": "Sí" if self.is_usable else "No",
        }


# Penalización aplicada a la puntuación según el tipo y la gravedad del
# problema. Los valores son juicios de criterio, no medidas: reflejan cuánto
# compromete cada defecto la fiabilidad de un backtest.
PENALIZACION: dict[tuple[IssueType, Severity], float] = {
    (IssueType.GAP, Severity.INFO): 1.0,
    (IssueType.GAP, Severity.WARNING): 6.0,
    (IssueType.GAP, Severity.CRITICAL): 25.0,
    (IssueType.STALE, Severity.WARNING): 10.0,
    (IssueType.STALE, Severity.CRITICAL): 30.0,
    (IssueType.OUTLIER, Severity.INFO): 1.0,
    (IssueType.OUTLIER, Severity.WARNING): 8.0,
    (IssueType.OUTLIER, Severity.CRITICAL): 20.0,
    (IssueType.DUPLICATE, Severity.CRITICAL): 40.0,
    (IssueType.OHLC_INVALID, Severity.CRITICAL): 40.0,
    (IssueType.NON_POSITIVE, Severity.CRITICAL): 100.0,
    (IssueType.SHORT_HISTORY, Severity.WARNING): 15.0,
    (IssueType.SHORT_HISTORY, Severity.CRITICAL): 50.0,
    (IssueType.STALE_FEED, Severity.WARNING): 12.0,
    (IssueType.STALE_FEED, Severity.CRITICAL): 35.0,
    (IssueType.STEP_CHANGE, Severity.WARNING): 12.0,
    (IssueType.STEP_CHANGE, Severity.CRITICAL): 30.0,
}


def compute_score(issues: list[QualityIssue]) -> float:
    """Calcula la puntuación de calidad a partir de los problemas hallados.

    Se parte de 100 y se descuenta. Un mismo tipo de problema no penaliza de
    forma proporcional a sus ocurrencias: veinte huecos no son veinte veces
    peor que uno, pero sí peor. Se aplica una raíz para reflejarlo.

    Args:
        issues: Problemas detectados.

    Returns:
        Puntuación entre 0 y 100.
    """
    puntuacion = 100.0
    for problema in issues:
        base = PENALIZACION.get((problema.issue_type, problema.severity), 5.0)
        escala = min(3.0, (problema.count ** 0.5))
        puntuacion -= base * escala / 1.7
    return max(0.0, min(100.0, puntuacion))
