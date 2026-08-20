"""Gráfico de una señal: precio, indicadores reales y niveles de la operación.

QUÉ MUESTRA Y POR QUÉ
----------------------
Cuando el sistema dice "comprar US500", la pregunta natural es *¿basándose en
qué?*. Este módulo responde dibujando exactamente lo que la estrategia usó para
decidir:

- El precio de las últimas sesiones
- Los indicadores concretos de esa estrategia, obtenidos de
  `chart_overlays()`, no una aproximación plausible
- Los tres niveles de la operación: entrada, invalidación y objetivo
- La zona de riesgo y la zona de beneficio, sombreadas

PRINCIPIO
---------
Si una estrategia no expone sus indicadores, el gráfico muestra sólo el precio
y lo declara. No se dibujan medias móviles genéricas "que quedan bien": una
media que la estrategia no usa haría creer al operador que la señal viene de
ahí, y eso es peor que no dibujar nada.

FORMATO
-------
Se devuelve una figura de Plotly, que es interactiva —zoom, valores al pasar el
ratón— y ya es dependencia del panel. Un PNG estático se vería igual pero no
permitiría inspeccionar.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLOR_PRECIO = "#e8eaf0"
COLOR_ENTRADA = "#4d9de0"
COLOR_STOP = "#e15554"
COLOR_OBJETIVO = "#3bb273"
COLORES_INDICADOR = ("#f0a202", "#7768ae", "#00a6a6", "#d81159", "#8ac926")


@dataclass(frozen=True)
class ChartLevels:
    """Niveles de la operación a marcar en el gráfico.

    Attributes:
        entry: Precio de entrada.
        stop: Nivel de invalidación.
        target: Objetivo, si existe.
        direction: 'long' o 'short'.
    """

    entry: Decimal
    stop: Decimal | None
    target: Decimal | None
    direction: str

    @property
    def is_long(self) -> bool:
        return self.direction.lower() in ("long", "compra")


def build_signal_chart(
    prices: pd.DataFrame,
    overlays: dict[str, pd.Series],
    levels: ChartLevels,
    symbol: str,
    strategy_label: str,
    bars: int = 180,
) -> go.Figure:
    """Construye el gráfico de una señal.

    Args:
        prices: Precios con columnas open/high/low/close.
        overlays: Indicadores de la estrategia, de `chart_overlays()`. Las
            claves con prefijo "panel:" van en un panel inferior aparte.
        levels: Niveles de entrada, invalidación y objetivo.
        symbol: Instrumento, para el título.
        strategy_label: Nombre legible de la estrategia.
        bars: Sesiones a mostrar.

    Returns:
        Figura de Plotly lista para `st.plotly_chart`.
    """
    df = prices.tail(bars)
    principales = {k: v for k, v in overlays.items() if not k.startswith("panel:")}
    inferiores = {
        k.removeprefix("panel:"): v
        for k, v in overlays.items()
        if k.startswith("panel:")
    }

    if inferiores:
        fig = make_subplots(
            rows=2, cols=1, shared_xaxes=True,
            row_heights=[0.72, 0.28], vertical_spacing=0.06,
        )
    else:
        fig = make_subplots(rows=1, cols=1)

    # --- Precio ---
    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["open"], high=df["high"], low=df["low"], close=df["close"],
            name=symbol,
            increasing_line_color=COLOR_OBJETIVO,
            decreasing_line_color=COLOR_STOP,
            showlegend=False,
        ),
        row=1, col=1,
    )

    # --- Indicadores sobre el precio ---
    for i, (nombre, serie) in enumerate(principales.items()):
        s = serie.reindex(df.index)
        fig.add_trace(
            go.Scatter(
                x=df.index, y=s, name=nombre, mode="lines",
                line={"width": 1.6, "color": COLORES_INDICADOR[i % len(COLORES_INDICADOR)]},
            ),
            row=1, col=1,
        )

    # --- Zonas de riesgo y beneficio ---
    entrada = float(levels.entry)
    if levels.stop is not None:
        fig.add_hrect(
            y0=min(entrada, float(levels.stop)),
            y1=max(entrada, float(levels.stop)),
            fillcolor=COLOR_STOP, opacity=0.09, line_width=0, row=1, col=1,
        )
    if levels.target is not None:
        fig.add_hrect(
            y0=min(entrada, float(levels.target)),
            y1=max(entrada, float(levels.target)),
            fillcolor=COLOR_OBJETIVO, opacity=0.09, line_width=0, row=1, col=1,
        )

    # --- Los tres niveles ---
    for precio, color, etiqueta in (
        (levels.entry, COLOR_ENTRADA, "Entrada"),
        (levels.stop, COLOR_STOP, "Invalidación"),
        (levels.target, COLOR_OBJETIVO, "Objetivo"),
    ):
        if precio is None:
            continue
        fig.add_hline(
            y=float(precio), line_dash="dash", line_color=color, line_width=1.5,
            annotation_text=f"{etiqueta}  {float(precio):,.2f}",
            annotation_position="right",
            annotation_font={"color": color, "size": 11},
            row=1, col=1,
        )

    # --- Panel inferior ---
    for i, (nombre, serie) in enumerate(inferiores.items()):
        s = serie.reindex(df.index)
        fig.add_trace(
            go.Scatter(
                x=df.index, y=s, name=nombre, mode="lines",
                line={"width": 1.6, "color": COLORES_INDICADOR[i % len(COLORES_INDICADOR)]},
            ),
            row=2, col=1,
        )
    if inferiores:
        fig.add_hline(y=0, line_color="#666", line_width=1, row=2, col=1)

    direccion = "COMPRA" if levels.is_long else "VENTA"
    fig.update_layout(
        title={
            "text": f"{symbol} · {direccion} · {strategy_label}",
            "font": {"size": 16},
        },
        height=560 if inferiores else 460,
        margin={"l": 10, "r": 110, "t": 50, "b": 10},
        xaxis_rangeslider_visible=False,
        template="plotly_dark",
        legend={
            "orientation": "h", "yanchor": "bottom", "y": 1.02,
            "xanchor": "left", "x": 0,
        },
        hovermode="x unified",
    )
    fig.update_yaxes(title_text="Precio", row=1, col=1)
    if inferiores:
        fig.update_yaxes(title_text="Indicador", row=2, col=1)

    return fig


def levels_summary(levels: ChartLevels, current_price: Decimal) -> list[dict]:
    """Resumen numérico de la operación, para la tabla junto al gráfico.

    Args:
        levels: Niveles de la operación.
        current_price: Precio actual.

    Returns:
        Filas con concepto, precio y distancia al precio actual.
    """
    filas = []
    actual = float(current_price)

    def fila(concepto: str, precio: Decimal | None) -> None:
        if precio is None:
            filas.append({"Nivel": concepto, "Precio": None, "Distancia": "—"})
            return
        p = float(precio)
        pct = (p - actual) / actual * 100 if actual else 0.0
        filas.append({
            "Nivel": concepto,
            "Precio": round(p, 4),
            "Distancia": f"{pct:+.2f}%",
        })

    fila("Precio actual", current_price)
    fila("Entrada", levels.entry)
    fila("Invalidación", levels.stop)
    fila("Objetivo", levels.target)

    if levels.stop is not None and levels.target is not None:
        riesgo = abs(float(levels.entry) - float(levels.stop))
        beneficio = abs(float(levels.target) - float(levels.entry))
        filas.append({
            "Nivel": "Beneficio / Riesgo",
            "Precio": None,
            "Distancia": f"{beneficio / riesgo:.2f} a 1" if riesgo > 0 else "—",
        })

    return filas
