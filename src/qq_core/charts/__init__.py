"""Gráficos del sistema.

`signal_chart` dibuja una señal con los indicadores REALES de su estrategia,
obtenidos de `chart_overlays()`. Si una estrategia no los expone, se muestra
sólo el precio y se declara: dibujar indicadores que la estrategia no usa haría
creer al operador que la señal viene de ahí.
"""

from qq_core.charts.signal_chart import (
    ChartLevels,
    build_signal_chart,
    levels_summary,
)

__all__ = ["ChartLevels", "build_signal_chart", "levels_summary"]
