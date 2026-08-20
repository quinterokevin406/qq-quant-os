"""Pruebas del control de calidad de datos (Módulo 02).

Este módulo es el que verifica la suposición sobre la que se apoya todo lo
demás: que los datos son correctos. Un error de datos no produce un fallo,
produce resultados creíbles y falsos.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from qq_core.quality.checks import (
    check_series,
    check_universe,
    universe_calendar,
    usable_symbols,
)
from qq_core.quality.issues import IssueType, Severity, compute_score


def _serie(n: int = 800, base: float = 5000.0, semilla: int = 11) -> pd.DataFrame:
    """Serie limpia de referencia."""
    np.random.seed(semilla)
    fechas = pd.bdate_range("2023-01-02", periods=n)
    px = base * np.exp(np.cumsum(np.random.normal(0.0003, 0.01, n)))
    return pd.DataFrame(
        {"open": px * 0.999, "high": px * 1.006, "low": px * 0.994, "close": px},
        index=fechas,
    )


def _hoy(frame: pd.DataFrame) -> date:
    return frame.index[-1].date()


# --------------------------------------------------------------------- #
# Serie correcta
# --------------------------------------------------------------------- #


def test_serie_limpia_puntua_alto() -> None:
    """CA-77: una serie sin defectos no debe generar falsos positivos.

    Un detector que marca problemas donde no los hay se vuelve ruido y se
    acaba ignorando, que es peor que no tenerlo.
    """
    frame = _serie()
    informe = check_series("LIMPIA", frame, today=_hoy(frame))
    assert informe.score >= 95
    assert informe.is_usable
    assert not informe.critical_issues


def test_serie_vacia_es_critica() -> None:
    informe = check_series("VACIA", pd.DataFrame())
    assert informe.score == 0.0
    assert not informe.is_usable


# --------------------------------------------------------------------- #
# Detección de cada tipo de problema
# --------------------------------------------------------------------- #


def test_detecta_precios_negativos() -> None:
    """CA-78: un precio negativo invalida la serie por completo.

    No es un defecto gradual: es imposible, y cualquier cálculo sobre él
    carece de sentido.
    """
    frame = _serie()
    frame.iloc[50, frame.columns.get_loc("close")] = -10.0
    informe = check_series("MALA", frame, today=_hoy(frame))

    tipos = {i.issue_type for i in informe.issues}
    assert IssueType.NON_POSITIVE in tipos
    assert not informe.is_usable


def test_detecta_serie_congelada() -> None:
    """CA-79: un precio repetido muchos días indica fallo del proveedor.

    Un mercado líquido no cierra doce sesiones exactamente al mismo precio.
    Cuando ocurre, el proveedor repitió el último dato conocido en lugar de
    admitir que no lo tenía.
    """
    frame = _serie()
    frame.iloc[100:112, :] = frame.iloc[99].values
    informe = check_series("CONGELADA", frame, today=_hoy(frame))

    congelada = next(
        i for i in informe.issues if i.issue_type is IssueType.STALE
    )
    assert congelada.severity is Severity.CRITICAL
    assert not informe.is_usable


def test_detecta_fechas_duplicadas() -> None:
    """Una sesión repetida distorsiona todas las medias móviles."""
    frame = _serie(100)
    duplicada = pd.concat([frame, frame.iloc[[50]]]).sort_index()
    informe = check_series("DUPLICADA", duplicada, today=_hoy(frame))

    assert any(
        i.issue_type is IssueType.DUPLICATE for i in informe.issues
    )
    assert not informe.is_usable


def test_detecta_barras_incoherentes() -> None:
    frame = _serie()
    frame.iloc[30, frame.columns.get_loc("high")] = 1.0
    informe = check_series("INCOHERENTE", frame, today=_hoy(frame))
    assert any(i.issue_type is IssueType.OHLC_INVALID for i in informe.issues)


def test_detecta_historico_corto() -> None:
    frame = _serie(80)
    informe = check_series("CORTA", frame, min_bars=250, today=_hoy(frame))
    assert any(i.issue_type is IssueType.SHORT_HISTORY for i in informe.issues)


def test_detecta_serie_desactualizada() -> None:
    """Una serie que dejó de recibir datos genera señales sobre el pasado.

    Analizada en solitario, sin universo con el que compararse, sólo puede
    avisarse: no hay forma de saber si el resto del mercado también está
    parado. La distinción entre «fallo del instrumento» y «descarga
    pendiente» requiere el contexto del universo.
    """
    frame = _serie()
    futuro = _hoy(frame) + timedelta(days=90)
    informe = check_series("VIEJA", frame, today=futuro)

    obsoleta = next(
        i for i in informe.issues if i.issue_type is IssueType.STALE_FEED
    )
    assert obsoleta.severity is Severity.WARNING
    assert "descarga pendiente" in obsoleta.detail


def test_no_marca_dias_volatiles_reales_como_error() -> None:
    """CA-80: el umbral de valores atípicos es deliberadamente alto.

    Los mercados tienen caídas del 10% reales. Marcarlas como error borraría
    precisamente los días que más importan para medir el riesgo.
    """
    frame = _serie()
    posicion = 400
    for columna in ("open", "high", "low", "close"):
        frame.iloc[posicion, frame.columns.get_loc(columna)] *= 0.90

    informe = check_series("CRASH", frame, today=_hoy(frame))
    atipicos = [i for i in informe.issues if i.issue_type is IssueType.OUTLIER]
    assert not atipicos or atipicos[0].severity is not Severity.CRITICAL


# --------------------------------------------------------------------- #
# Huecos y calendario
# --------------------------------------------------------------------- #


def test_calendario_del_universo_ignora_rarezas() -> None:
    """Una fecha que sólo tiene un mercado no se considera día hábil."""
    base = _serie(200)
    rara = base.copy()
    extra = pd.DataFrame(
        {"open": [1.0], "high": [1.0], "low": [1.0], "close": [1.0]},
        index=[pd.Timestamp("2050-01-03")],
    )
    rara = pd.concat([rara, extra])

    calendario = universe_calendar({"A": base, "B": base.copy(), "C": rara})
    assert date(2050, 1, 3) not in calendario


def test_festivo_comun_no_se_marca_como_hueco() -> None:
    """CA-81: si falta en todos los mercados, fue festivo, no un fallo.

    Es la distinción central del módulo: sin ella, cada día de Navidad
    aparecería como veintitrés errores de datos.
    """
    base = _serie(300)
    sin_festivo = base.drop(base.index[100])
    universo = {f"S{i}": sin_festivo.copy() for i in range(5)}

    informes = check_universe(universo, today=_hoy(base))
    for informe in informes.values():
        huecos = [i for i in informe.issues if i.issue_type is IssueType.GAP]
        assert not huecos


def test_hueco_en_un_solo_mercado_si_se_marca() -> None:
    """Si sólo falta en uno, es un fallo de datos de ese instrumento."""
    base = _serie(300)
    universo = {f"S{i}": base.copy() for i in range(4)}
    universo["ROTA"] = base.drop(base.index[100:160])

    informes = check_universe(universo, today=_hoy(base))
    huecos = [
        i for i in informes["ROTA"].issues if i.issue_type is IssueType.GAP
    ]
    assert huecos
    assert not [
        i for i in informes["S0"].issues if i.issue_type is IssueType.GAP
    ]


# --------------------------------------------------------------------- #
# Puntuación y consecuencias
# --------------------------------------------------------------------- #


def test_puntuacion_sin_problemas_es_maxima() -> None:
    assert compute_score([]) == 100.0


def test_puntuacion_nunca_es_negativa() -> None:
    """Aunque se acumulen muchos defectos, la escala se mantiene."""
    from qq_core.quality.issues import QualityIssue

    muchos = [
        QualityIssue(
            symbol="X", issue_type=IssueType.NON_POSITIVE,
            severity=Severity.CRITICAL, count=100, detail="prueba",
        )
        for _ in range(10)
    ]
    assert compute_score(muchos) == 0.0


def test_un_problema_critico_invalida_aunque_puntue_alto() -> None:
    """CA-82: una serie con precios negativos no es «un poco» inutilizable.

    La puntuación es orientativa; un problema crítico es descalificante por
    sí solo, sin promediarse con lo que esté bien.
    """
    from qq_core.quality.issues import QualityIssue, QualityReport

    informe = QualityReport(
        symbol="X", bars=1000, first_date=date(2020, 1, 1),
        last_date=date(2026, 1, 1),
        issues=[
            QualityIssue(
                symbol="X", issue_type=IssueType.NON_POSITIVE,
                severity=Severity.CRITICAL, count=1, detail="prueba",
            )
        ],
        score=95.0,
    )
    assert not informe.is_usable


def test_series_defectuosas_se_excluyen_del_universo() -> None:
    """CA-83: no basta con avisar, hay que dejar de usar la serie.

    Es la consecuencia práctica del módulo: si una serie no es fiable, el
    sistema deja de generar señales sobre ella en lugar de fingir que todo
    está bien.
    """
    buena = _serie()
    mala = _serie()
    mala.iloc[50, mala.columns.get_loc("close")] = -1.0

    informes = check_universe(
        {"BUENA": buena, "MALA": mala}, today=_hoy(buena)
    )
    aptas = usable_symbols(informes)
    assert "BUENA" in aptas
    assert "MALA" not in aptas


def test_cada_problema_se_explica_en_lenguaje_llano() -> None:
    """El operador debe entender qué pasa sin leer el código."""
    frame = _serie()
    frame.iloc[100:112, :] = frame.iloc[99].values
    informe = check_series("X", frame, today=_hoy(frame))

    for problema in informe.issues:
        assert len(problema.detail) > 40
        assert problema.issue_type.value in problema.to_row()["Problema"]


def test_informe_tiene_semaforo() -> None:
    """El panel necesita un color y una etiqueta legible por instrumento."""
    frame = _serie()
    informe = check_series("X", frame, today=_hoy(frame))
    assert informe.status_color.startswith("#")
    assert informe.status_label in (
        "Correcta", "Aceptable", "Con reservas", "Deficiente", "No utilizable"
    )


# --------------------------------------------------------------------- #
# Distinción entre fallo del dato y descarga pendiente
# --------------------------------------------------------------------- #


def test_descarga_pendiente_no_inutiliza_el_sistema() -> None:
    """CA-84: si TODO está atrasado, es operación, no calidad del dato.

    Sin esta distinción, volver de dos semanas de vacaciones dejaría el
    sistema entero sin generar señales. Es un modo de fallo peligroso: el
    panel debe seguir funcionando y avisar de que los datos son antiguos.
    """
    base = _serie(400)
    universo = {f"S{i}": base.copy() for i in range(5)}
    muy_despues = _hoy(base) + timedelta(days=45)

    informes = check_universe(universo, today=muy_despues)

    for informe in informes.values():
        obsoletas = [
            i for i in informe.issues if i.issue_type is IssueType.STALE_FEED
        ]
        assert obsoletas, "debe avisarse de que los datos son antiguos"
        assert obsoletas[0].severity is Severity.WARNING
        assert informe.is_usable, "el sistema debe seguir operativo"


def test_serie_rezagada_frente_al_resto_si_es_critica() -> None:
    """CA-85: si sólo una serie se quedó atrás, esa serie está rota.

    Es el caso contrario al anterior: las demás se actualizaron y ésta no,
    así que el problema es del instrumento y no debe usarse.
    """
    base = _serie(400)
    rezagada = base.iloc[:-60]  # se quedó 60 sesiones atrás

    universo = {f"S{i}": base.copy() for i in range(4)}
    universo["REZAGADA"] = rezagada

    informes = check_universe(universo, today=_hoy(base))

    obsoleta = next(
        i for i in informes["REZAGADA"].issues
        if i.issue_type is IssueType.STALE_FEED
    )
    assert obsoleta.severity is Severity.CRITICAL
    assert not informes["REZAGADA"].is_usable
    assert informes["S0"].is_usable


def test_series_al_dia_no_generan_aviso() -> None:
    base = _serie(400)
    informes = check_universe({"A": base, "B": base.copy()}, today=_hoy(base))
    for informe in informes.values():
        assert not [
            i for i in informe.issues if i.issue_type is IssueType.STALE_FEED
        ]
