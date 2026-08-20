"""Comprobaciones de calidad sobre las series de precios (Módulo 02).

CÓMO SE DISTINGUE UN FESTIVO DE UN DATO QUE FALTA
--------------------------------------------------
Es la dificultad central del módulo. Un hueco en la serie puede ser:

  - Un día de mercado cerrado, que es normal y no es un problema.
  - Una sesión que el proveedor no entregó, que sí lo es.

Sin el calendario oficial de cada bolsa no se puede saber con certeza. La
aproximación que se usa aquí es estadística: se comparan los huecos de un
instrumento con los del resto del universo en las mismas fechas. Si diez
mercados carecen del mismo día, fue festivo; si sólo falta en uno, es un fallo
de datos.

Es imperfecta y se declara como tal. La solución exacta requiere calendarios
de bolsa, que es una dependencia externa que no compensa todavía.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from qq_core.quality.issues import (
    IssueType,
    QualityIssue,
    QualityReport,
    Severity,
    compute_score,
)

MAX_FECHAS_REPORTADAS = 20
"""Fechas concretas que se guardan por problema. Suficiente para investigar,
sin llenar la base de datos cuando una serie está muy dañada."""


def check_series(
    symbol: str,
    frame: pd.DataFrame,
    universe_dates: set[date] | None = None,
    min_bars: int = 250,
    max_staleness_days: int = 7,
    today: date | None = None,
    universe_last_date: date | None = None,
) -> QualityReport:
    """Analiza una serie de precios y devuelve su diagnóstico.

    Args:
        symbol: Instrumento analizado.
        frame: Precios con columnas open, high, low, close.
        universe_dates: Fechas presentes en la mayoría del universo. Permite
            distinguir festivos de datos ausentes.
        universe_last_date: Fecha más reciente de todo el universo. Permite
            distinguir «falta ejecutar la descarga» de «esta serie concreta
            dejó de actualizarse».
        min_bars: Barras mínimas para considerar el histórico suficiente.
        max_staleness_days: Días sin actualizar a partir de los cuales se
            considera que la serie ha dejado de recibir datos.
        today: Fecha de referencia, para pruebas.

    Returns:
        Informe con los problemas encontrados y la puntuación resultante.
    """
    hoy = today or date.today()

    if frame.empty:
        return QualityReport(
            symbol=symbol, bars=0, first_date=None, last_date=None,
            issues=[
                QualityIssue(
                    symbol=symbol, issue_type=IssueType.SHORT_HISTORY,
                    severity=Severity.CRITICAL, count=1,
                    detail="La serie está vacía: no hay ningún dato.",
                )
            ],
            score=0.0,
        )

    fechas = [d.date() if hasattr(d, "date") else d for d in frame.index]
    problemas: list[QualityIssue] = []

    problemas += _check_prices(symbol, frame)
    problemas += _check_ohlc(symbol, frame)
    problemas += _check_duplicates(symbol, fechas)
    problemas += _check_history_length(symbol, frame, min_bars)
    problemas += _check_staleness(
        symbol, fechas[-1], hoy, max_staleness_days, universe_last_date
    )
    problemas += _check_gaps(symbol, fechas, universe_dates)
    problemas += _check_stale_prices(symbol, frame)
    problemas += _check_outliers(symbol, frame)
    problemas += _check_step_changes(symbol, frame)

    return QualityReport(
        symbol=symbol,
        bars=len(frame),
        first_date=fechas[0],
        last_date=fechas[-1],
        issues=problemas,
        score=compute_score(problemas),
    )


# --------------------------------------------------------------------- #
# Comprobaciones individuales
# --------------------------------------------------------------------- #


def _check_prices(symbol: str, frame: pd.DataFrame) -> list[QualityIssue]:
    """Precios cero o negativos. Siempre es un error del proveedor."""
    invalidos = frame[
        (frame[["open", "high", "low", "close"]] <= 0).any(axis=1)
    ]
    if invalidos.empty:
        return []
    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.NON_POSITIVE,
            severity=Severity.CRITICAL, count=len(invalidos),
            detail=(
                f"{len(invalidos)} sesiones con precio cero o negativo. Es "
                f"siempre un error del proveedor; la serie no puede usarse."
            ),
            dates=tuple(_fechas(invalidos.index)),
        )
    ]


def _check_ohlc(symbol: str, frame: pd.DataFrame) -> list[QualityIssue]:
    """Barras estructuralmente imposibles.

    El modelo `Bar` ya valida esto en la ingesta, así que encontrar algo aquí
    indica que los datos entraron por otra vía o que la validación falló.
    """
    incoherentes = frame[
        (frame["high"] < frame["low"])
        | (frame["high"] < frame[["open", "close"]].max(axis=1))
        | (frame["low"] > frame[["open", "close"]].min(axis=1))
    ]
    if incoherentes.empty:
        return []
    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.OHLC_INVALID,
            severity=Severity.CRITICAL, count=len(incoherentes),
            detail=(
                f"{len(incoherentes)} barras con máximo, mínimo y cierre "
                f"incompatibles entre sí."
            ),
            dates=tuple(_fechas(incoherentes.index)),
        )
    ]


def _check_duplicates(symbol: str, fechas: list[date]) -> list[QualityIssue]:
    """Sesiones repetidas.

    Una fecha duplicada distorsiona todas las medias móviles y hace que el
    backtest opere dos veces el mismo día.
    """
    serie = pd.Series(fechas)
    repetidas = serie[serie.duplicated()]
    if repetidas.empty:
        return []
    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.DUPLICATE,
            severity=Severity.CRITICAL, count=len(repetidas),
            detail=(
                f"{len(repetidas)} fechas aparecen más de una vez. Distorsiona "
                f"las medias móviles y duplica operaciones en el backtest."
            ),
            dates=tuple(repetidas.unique()[:MAX_FECHAS_REPORTADAS]),
        )
    ]


def _check_history_length(
    symbol: str, frame: pd.DataFrame, min_bars: int
) -> list[QualityIssue]:
    """Histórico suficiente para las estrategias configuradas."""
    if len(frame) >= min_bars:
        return []
    gravedad = (
        Severity.CRITICAL if len(frame) < min_bars * 0.4 else Severity.WARNING
    )
    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.SHORT_HISTORY,
            severity=gravedad, count=1,
            detail=(
                f"Sólo {len(frame)} sesiones disponibles frente a las "
                f"{min_bars} necesarias. Las estrategias de ventana larga no "
                f"pueden calcularse."
            ),
            metric=float(len(frame)),
        )
    ]


def _check_staleness(
    symbol: str,
    ultima: date,
    hoy: date,
    maximo: int,
    ultima_del_universo: date | None = None,
) -> list[QualityIssue]:
    """Si la serie ha dejado de recibir actualizaciones.

    DISTINCIÓN IMPORTANTE
    ---------------------
    Que los datos estén viejos puede significar dos cosas muy distintas:

      - **Nadie ha ejecutado la descarga.** Afecta a todas las series por
        igual. Es un problema de operación, no de calidad del dato, y no debe
        inutilizar el sistema entero: si alguien vuelve de vacaciones, el
        panel debe seguir funcionando y avisar de que los datos son antiguos.

      - **Esta serie concreta dejó de actualizarse** mientras las demás sí lo
        hacían. Eso sí es un fallo del instrumento: el identificador del
        proveedor cambió o el mercado dejó de cotizar.

    Se distinguen comparando con la fecha más reciente del universo.
    """
    dias = (hoy - ultima).days
    if dias <= maximo:
        return []

    # Retraso propio de esta serie frente al resto del universo.
    rezago = (ultima_del_universo - ultima).days if ultima_del_universo else 0

    if rezago > maximo:
        # La serie va por detrás de las demás: fallo de este instrumento.
        gravedad = Severity.CRITICAL if rezago > maximo * 3 else Severity.WARNING
        detalle = (
            f"El último dato es del {ultima.isoformat()}, {rezago} días por "
            f"detrás del resto del universo. El identificador del proveedor "
            f"pudo cambiar, o el instrumento dejó de cotizar."
        )
    else:
        # Todo el universo está igual de atrasado: falta ejecutar la descarga.
        gravedad = Severity.WARNING
        detalle = (
            f"El último dato es del {ultima.isoformat()}, hace {dias} días. "
            f"Todo el universo está igual de atrasado, así que se trata de una "
            f"descarga pendiente, no de un fallo de esta serie. Las señales se "
            f"están calculando sobre datos antiguos."
        )

    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.STALE_FEED,
            severity=gravedad, count=1, detail=detalle,
            dates=(ultima,), metric=float(dias),
        )
    ]


def _check_gaps(
    symbol: str, fechas: list[date], universe_dates: set[date] | None
) -> list[QualityIssue]:
    """Sesiones ausentes que otros mercados sí registraron.

    Sin el calendario oficial de la bolsa no se puede saber con certeza si un
    día faltaba por festivo. La aproximación es comparar con el resto del
    universo: si la mayoría de mercados tienen ese día y este no, falta.
    """
    if not universe_dates:
        return []

    presentes = set(fechas)
    dentro_del_rango = {
        d for d in universe_dates if fechas[0] <= d <= fechas[-1]
    }
    ausentes = sorted(dentro_del_rango - presentes)
    if not ausentes:
        return []

    proporcion = len(ausentes) / max(len(dentro_del_rango), 1)
    if proporcion > 0.15:
        gravedad, nota = Severity.CRITICAL, "compromete cualquier análisis"
    elif proporcion > 0.04:
        gravedad, nota = Severity.WARNING, "puede distorsionar los indicadores"
    else:
        gravedad, nota = Severity.INFO, "sin impacto relevante"

    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.GAP,
            severity=gravedad, count=len(ausentes),
            detail=(
                f"Faltan {len(ausentes)} sesiones que la mayoría de mercados "
                f"sí registró ({proporcion:.1%} del periodo); {nota}."
            ),
            dates=tuple(ausentes[:MAX_FECHAS_REPORTADAS]),
            metric=proporcion * 100,
        )
    ]


def _check_stale_prices(
    symbol: str, frame: pd.DataFrame, min_repeticiones: int = 4
) -> list[QualityIssue]:
    """Precio idéntico repetido varias sesiones seguidas.

    Un mercado líquido no cierra tres días exactamente al mismo precio. Cuando
    ocurre, casi siempre significa que el proveedor repitió el último dato
    conocido en lugar de reconocer que no lo tenía.
    """
    cierre = frame["close"]
    grupos = (cierre != cierre.shift(1)).cumsum()
    tramos = cierre.groupby(grupos).size()
    largos = tramos[tramos >= min_repeticiones]
    if largos.empty:
        return []

    maximo = int(largos.max())
    afectadas = int(largos.sum())
    gravedad = (
        Severity.CRITICAL if maximo >= 10 or afectadas > len(frame) * 0.1
        else Severity.WARNING
    )
    fechas_afectadas = [
        _a_fecha(idx) for idx, g in zip(cierre.index, grupos)
        if g in largos.index
    ]
    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.STALE,
            severity=gravedad, count=afectadas,
            detail=(
                f"{afectadas} sesiones con el precio de cierre repetido, la "
                f"racha más larga de {maximo} días seguidos. Suele indicar que "
                f"el proveedor repitió el último dato conocido."
            ),
            dates=tuple(fechas_afectadas[:MAX_FECHAS_REPORTADAS]),
            metric=float(maximo),
        )
    ]


def _check_outliers(
    symbol: str, frame: pd.DataFrame, sigmas: float = 8.0
) -> list[QualityIssue]:
    """Saltos de precio incompatibles con la volatilidad del instrumento.

    Se usa un umbral muy alto —ocho desviaciones típicas— a propósito. Los
    mercados tienen movimientos extremos reales, y marcarlos como error
    borraría precisamente los días que más importan para medir el riesgo. Lo
    que se busca aquí no son días volátiles, sino errores de tecleo del
    proveedor.
    """
    retornos = frame["close"].pct_change()
    if retornos.notna().sum() < 60:
        return []

    desviacion = retornos.rolling(60, min_periods=40).std()
    z = (retornos / desviacion).abs()
    atipicos = frame[z > sigmas]
    if atipicos.empty:
        return []

    peor = float(retornos[z > sigmas].abs().max() * 100)
    gravedad = (
        Severity.CRITICAL if len(atipicos) > 5
        else Severity.WARNING if len(atipicos) > 1
        else Severity.INFO
    )
    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.OUTLIER,
            severity=gravedad, count=len(atipicos),
            detail=(
                f"{len(atipicos)} sesiones con un salto superior a {sigmas:.0f} "
                f"desviaciones típicas; el mayor fue del {peor:.1f}%. Este "
                f"umbral no marca días volátiles reales, sino errores del "
                f"proveedor."
            ),
            dates=tuple(_fechas(atipicos.index)),
            metric=peor,
        )
    ]


def _check_step_changes(
    symbol: str, frame: pd.DataFrame, umbral: float = 0.25
) -> list[QualityIssue]:
    """Cambios bruscos y permanentes del nivel de precios.

    Distinto de un valor atípico: aquí el precio salta y se queda en el nuevo
    nivel. Es la firma de una división de acciones no ajustada o de un cambio
    de contrato de futuros mal empalmado.
    """
    cierre = frame["close"]
    if len(cierre) < 30:
        return []

    media_previa = cierre.rolling(10).mean().shift(1)
    media_posterior = cierre.shift(-9).rolling(10).mean().shift(-1)
    salto = ((media_posterior - media_previa) / media_previa).abs()
    sospechosos = frame[salto > umbral]
    if sospechosos.empty:
        return []

    return [
        QualityIssue(
            symbol=symbol, issue_type=IssueType.STEP_CHANGE,
            severity=Severity.WARNING, count=len(sospechosos),
            detail=(
                f"{len(sospechosos)} cambios de nivel superiores al "
                f"{umbral:.0%} que se mantienen después. Puede indicar una "
                f"división de acciones sin ajustar o un empalme incorrecto de "
                f"contratos."
            ),
            dates=tuple(_fechas(sospechosos.index)),
        )
    ]


# --------------------------------------------------------------------- #
# Utilidades
# --------------------------------------------------------------------- #


def _a_fecha(valor) -> date:
    return valor.date() if hasattr(valor, "date") else valor


def _fechas(indice) -> list[date]:
    return [_a_fecha(d) for d in indice[:MAX_FECHAS_REPORTADAS]]


def universe_calendar(
    frames: dict[str, pd.DataFrame], min_share: float = 0.6
) -> set[date]:
    """Fechas en que la mayoría del universo tuvo sesión.

    Es la aproximación del módulo al calendario de mercado: en lugar de
    consultar los festivos oficiales de cada bolsa, se asume que un día en que
    la mayoría de los mercados operó fue un día hábil.

    Args:
        frames: Precios por instrumento.
        min_share: Proporción del universo que debe tener la fecha.

    Returns:
        Conjunto de fechas consideradas hábiles.
    """
    if not frames:
        return set()

    conteo: dict[date, int] = {}
    for frame in frames.values():
        for d in frame.index:
            fecha = _a_fecha(d)
            conteo[fecha] = conteo.get(fecha, 0) + 1

    # Se redondea hacia ARRIBA: con `int()`, tres series y un umbral del 60%
    # exigirían sólo una coincidencia (int(1.8) = 1), lo que contradice el
    # propio umbral y hacía pasar por día hábil cualquier fecha suelta.
    minimo = max(1, math.ceil(len(frames) * min_share))
    return {fecha for fecha, n in conteo.items() if n >= minimo}


def check_universe(
    frames: dict[str, pd.DataFrame],
    min_bars: int = 250,
    today: date | None = None,
) -> dict[str, QualityReport]:
    """Analiza todas las series del universo.

    Args:
        frames: Precios por instrumento.
        min_bars: Barras mínimas exigidas.
        today: Fecha de referencia.

    Returns:
        Informe por instrumento.
    """
    calendario = universe_calendar(frames)
    ultima_global = max(
        (_a_fecha(frame.index[-1]) for frame in frames.values() if len(frame)),
        default=None,
    )
    return {
        symbol: check_series(
            symbol, frame, universe_dates=calendario,
            min_bars=min_bars, today=today,
            universe_last_date=ultima_global,
        )
        for symbol, frame in frames.items()
    }


def usable_symbols(reports: dict[str, QualityReport]) -> set[str]:
    """Instrumentos aptos para generar señales.

    El resto se excluye del análisis. Es la consecuencia práctica del módulo:
    no basta con avisar de que una serie está mal, hay que dejar de usarla.
    """
    return {s for s, r in reports.items() if r.is_usable}
