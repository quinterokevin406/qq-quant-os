"""Motor de indicadores (Módulo 03).

EL PROBLEMA QUE ESTE MÓDULO EXISTE PARA EVITAR
-----------------------------------------------
El sesgo de anticipación (*lookahead bias*) es el error más caro de la
investigación cuantitativa, y el más difícil de detectar: no produce ningún
fallo. Produce backtests excelentes que nunca se reproducen en real.

Ocurre cuando un indicador usa, aunque sea indirectamente, información que no
estaba disponible en el momento de la decisión. Ejemplos reales y frecuentes:

  - Usar el cierre del día para decidir una entrada ejecutada ese mismo día.
  - Normalizar una serie con su media histórica completa, que incluye el futuro.
  - Rellenar un dato faltante con el valor siguiente en lugar del anterior.

REGLA ESTRUCTURAL DE ESTE MÓDULO
---------------------------------
Todo indicador calculado para la barra `t` usa exclusivamente información de
barras `<= t`. La señal generada en `t` se ejecuta al precio de apertura de
`t+1`. El motor de backtesting aplica ese desplazamiento de forma obligatoria
y no configurable: no existe un parámetro para desactivarlo.

Las funciones de este módulo son deliberadamente simples y verificables una a
una. La complejidad se paga en imposibilidad de auditar.
"""

from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Media móvil simple.

    Args:
        series: Serie de precios indexada por fecha ascendente.
        window: Número de observaciones.

    Returns:
        Serie con los primeros `window - 1` valores como NaN. No se rellenan:
        un NaN es información honesta ("aún no hay datos suficientes"),
        mientras que rellenarlo inventa una señal donde no la hay.
    """
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, window: int) -> pd.Series:
    """Media móvil exponencial.

    Usa `adjust=False` para que el valor en `t` dependa sólo del valor previo
    y del dato actual. Con `adjust=True`, recalcular la serie con más histórico
    cambiaría los valores pasados, y el backtest dejaría de ser reproducible.
    """
    return series.ewm(span=window, adjust=False, min_periods=window).mean()


def returns(series: pd.Series, periods: int = 1) -> pd.Series:
    """Rentabilidad porcentual sobre `periods` barras."""
    return series.pct_change(periods=periods)


def log_returns(series: pd.Series) -> pd.Series:
    """Rentabilidad logarítmica. Aditiva en el tiempo, preferible para
    agregación y para medir volatilidad."""
    import numpy as np

    return pd.Series(
        np.log(series / series.shift(1)), index=series.index, name=series.name
    )


def realized_volatility(
    series: pd.Series, window: int = 20, annualize: bool = True
) -> pd.Series:
    """Volatilidad realizada anualizada.

    Args:
        series: Serie de precios de cierre.
        window: Ventana de cálculo en barras.
        annualize: Si escalar a base anual asumiendo 252 sesiones.

    Returns:
        Volatilidad en tanto por uno (0.15 = 15% anual).
    """
    vol = log_returns(series).rolling(window=window, min_periods=window).std()
    return vol * (252 ** 0.5) if annualize else vol


def atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14
) -> pd.Series:
    """Rango verdadero medio (Average True Range).

    Mide volatilidad en unidades de precio, no porcentuales. Es lo que permite
    situar niveles de invalidación proporcionales al comportamiento reciente
    del instrumento en lugar de a un porcentaje arbitrario.
    """
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return true_range.rolling(window=window, min_periods=window).mean()


def zscore(series: pd.Series, window: int) -> pd.Series:
    """Distancia a la media en desviaciones típicas, con ventana móvil.

    Se usa ventana móvil y no estadísticos de toda la serie precisamente para
    evitar el sesgo de anticipación: la media de toda la muestra incluye datos
    futuros que no estaban disponibles en el momento de la decisión.
    """
    rolling = series.rolling(window=window, min_periods=window)
    mean = rolling.mean()
    std = rolling.std()
    return (series - mean) / std.replace(0, pd.NA)


def rolling_rank(frame: pd.DataFrame, window: int) -> pd.DataFrame:
    """Clasificación transversal por rentabilidad acumulada.

    Args:
        frame: Precios de cierre, una columna por instrumento.
        window: Ventana de la rentabilidad usada para clasificar.

    Returns:
        Percentil de cada instrumento (0 = peor, 1 = mejor) en cada fecha.
    """
    momentum = frame.pct_change(periods=window)
    return momentum.rank(axis=1, pct=True)


def drawdown(equity: pd.Series) -> pd.Series:
    """Caída desde el máximo histórico previo, en tanto por uno."""
    return equity / equity.cummax() - 1.0


def bars_to_frame(bars) -> pd.DataFrame:
    """Convierte una lista de `Bar` en DataFrame ordenado por fecha.

    Punto de conversión único entre el dominio (`Decimal`, exacto) y el cálculo
    numérico (`float`, rápido). Concentrarlo aquí hace que la pérdida de
    precisión ocurra en un solo lugar identificable, y sólo para el cálculo de
    indicadores, nunca para el registro contable.
    """
    if not bars:
        return pd.DataFrame(
            columns=["open", "high", "low", "close", "volume"]
        ).astype(float)
    frame = pd.DataFrame(
        {
            "open": [float(b.open) for b in bars],
            "high": [float(b.high) for b in bars],
            "low": [float(b.low) for b in bars],
            "close": [float(b.close) for b in bars],
            "volume": [float(b.volume) if b.volume is not None else float("nan")
                       for b in bars],
        },
        index=pd.DatetimeIndex([b.ts for b in bars], name="fecha"),
    )
    return frame.sort_index()


def assert_no_lookahead(compute, source: pd.Series, tolerance: float = 1e-9) -> None:
    """Verifica que un indicador no usa información futura.

    Recalcula el indicador truncando la serie en varios puntos y comprueba que
    el valor en cada punto coincide con el obtenido usando la serie completa.
    Si difiere, el indicador está mirando hacia adelante.

    Args:
        compute: Función que recibe una serie y devuelve el indicador.
        source: Serie de entrada.
        tolerance: Diferencia máxima admitida por redondeo en coma flotante.

    Raises:
        AssertionError: Si el indicador cambia al conocerse datos futuros.

    Example:
        >>> assert_no_lookahead(lambda s: sma(s, 20), precios)
    """
    completo = compute(source).dropna()
    if len(completo) < 3:
        return

    for pos in (len(completo) // 3, len(completo) // 2, len(completo) - 1):
        fecha = completo.index[pos]
        truncado = compute(source.loc[:fecha]).dropna()
        if fecha not in truncado.index:
            raise AssertionError(
                f"El indicador no produce valor en {fecha} con datos truncados"
            )
        diferencia = abs(float(truncado.loc[fecha]) - float(completo.loc[fecha]))
        if diferencia > tolerance:
            raise AssertionError(
                f"SESGO DE ANTICIPACIÓN detectado en {fecha}: "
                f"con serie completa={completo.loc[fecha]:.10f}, "
                f"con datos disponibles hasta esa fecha={truncado.loc[fecha]:.10f}"
            )
