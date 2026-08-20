"""Gestión de salidas: stop fijo, trailing stop y objetivo.

POR QUÉ EL TRAILING STOP MEJORA DE VERDAD
------------------------------------------
La mayoría de estrategias de tendencia no fallan al entrar: fallan al salir.
Con un stop fijo, una posición que sube un 20% y luego se gira acaba cerrando
en la pérdida inicial, devolviendo todo el beneficio.

El trailing stop resuelve eso moviendo el nivel de invalidación a favor de la
posición conforme el precio avanza, sin moverlo nunca en contra. Convierte
una operación ganadora en una que no puede volver a perder.

Es una mejora legítima, a diferencia de ajustar parámetros hasta que el
backtest salga verde. Cambia la estructura de la operación, no la selección
de la muestra.

LO QUE NO HACE
--------------
No mejora la tasa de acierto: al contrario, la empeora, porque cierra
posiciones que habrían recuperado. Lo que mejora es la relación entre lo que
se gana y lo que se pierde. Un sistema con trailing stop acierta menos veces
pero gana más dinero, y eso desconcierta a quien mire sólo el porcentaje de
aciertos.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pandas as pd

from qq_core.domain.signal import Direction


@dataclass(frozen=True)
class ExitPolicy:
    """Reglas de salida de una posición.

    Attributes:
        stop_atr_multiple: Distancia inicial de la invalidación, en múltiplos
            de volatilidad reciente.
        target_atr_multiple: Distancia del objetivo. `None` para dejar correr
            la posición sin objetivo fijo, que es lo habitual en estrategias
            de tendencia.
        trailing_enabled: Si el stop acompaña al precio.
        trailing_atr_multiple: Distancia a la que se mantiene el trailing
            stop respecto al máximo alcanzado.
        trailing_activation_atr: Beneficio, en múltiplos de volatilidad, que
            debe acumularse antes de activar el trailing. Activarlo desde el
            primer momento cierra posiciones por ruido normal del mercado
            antes de que la tendencia tenga ocasión de desarrollarse.
        breakeven_at_atr: Beneficio al que el stop se mueve al precio de
            entrada, eliminando el riesgo de la operación. `None` desactiva
            esta regla.
    """

    stop_atr_multiple: float = 2.0
    target_atr_multiple: float | None = None
    trailing_enabled: bool = True
    trailing_atr_multiple: float = 2.5
    trailing_activation_atr: float = 1.0
    breakeven_at_atr: float | None = 1.5


def apply_exit_policy(
    prices: pd.DataFrame,
    positions: pd.Series,
    atr: pd.Series,
    policy: ExitPolicy,
) -> tuple[pd.Series, pd.Series]:
    """Aplica las reglas de salida sobre las posiciones de una estrategia.

    Recorre la serie manteniendo el estado de cada operación: precio de
    entrada, máximo alcanzado y nivel de stop vigente. Cuando el precio toca
    el stop, la posición se cierra aunque la estrategia siguiera indicando
    mantenerla.

    Args:
        prices: Precios con columnas open, high, low, close.
        positions: Posiciones deseadas por la estrategia (+1 / 0 / -1), ya
            desplazadas para reflejar el desfase de ejecución.
        atr: Volatilidad reciente, en unidades de precio.
        policy: Reglas de salida a aplicar.

    Returns:
        Tupla `(posiciones_efectivas, motivos)`. El segundo elemento indica
        por qué se cerró cada posición, para poder analizarlo después.

    Note:
        El stop se comprueba contra el mínimo (en compras) o el máximo (en
        ventas) de la sesión, no contra el cierre. Comprobarlo sólo contra el
        cierre ignoraría que el precio pasó por ese nivel durante el día y
        produciría un backtest optimista.
    """
    close = prices["close"].to_numpy(dtype=float)
    alto = prices["high"].to_numpy(dtype=float)
    bajo = prices["low"].to_numpy(dtype=float)
    pos = positions.to_numpy(dtype=float)
    volatilidad = atr.to_numpy(dtype=float)

    n = len(close)
    efectiva = np.zeros(n)
    motivos = np.array([""] * n, dtype=object)

    estado = 0.0
    entrada = 0.0
    extremo = 0.0
    stop = 0.0
    objetivo = 0.0
    vol_entrada = 0.0
    breakeven_aplicado = False

    for i in range(n):
        deseada = pos[i]
        v = volatilidad[i]

        # ---------------- Sin posición abierta ---------------- #
        if estado == 0.0:
            if deseada != 0.0 and not np.isnan(v) and v > 0:
                estado = deseada
                entrada = close[i]
                extremo = close[i]
                vol_entrada = v
                breakeven_aplicado = False
                distancia = v * policy.stop_atr_multiple
                stop = entrada - distancia if estado > 0 else entrada + distancia
                objetivo = (
                    entrada + v * policy.target_atr_multiple * estado
                    if policy.target_atr_multiple else np.nan
                )
            efectiva[i] = estado
            continue

        # ---------------- Posición abierta ---------------- #
        # 1. ¿Tocó el stop durante la sesión?
        tocado = (estado > 0 and bajo[i] <= stop) or (estado < 0 and alto[i] >= stop)
        if tocado:
            motivos[i] = "stop"
            estado = 0.0
            efectiva[i] = 0.0
            continue

        # 2. ¿Alcanzó el objetivo?
        if not np.isnan(objetivo):
            llegó = (estado > 0 and alto[i] >= objetivo) or (
                estado < 0 and bajo[i] <= objetivo
            )
            if llegó:
                motivos[i] = "objetivo"
                estado = 0.0
                efectiva[i] = 0.0
                continue

        # 3. ¿La estrategia dejó de querer la posición?
        if deseada != estado:
            motivos[i] = "señal"
            estado = 0.0
            efectiva[i] = 0.0
            continue

        # 4. Actualizar niveles a favor de la posición
        extremo = max(extremo, alto[i]) if estado > 0 else min(extremo, bajo[i])
        beneficio = (extremo - entrada) * estado

        if (
            policy.breakeven_at_atr is not None
            and not breakeven_aplicado
            and vol_entrada > 0
            and beneficio >= vol_entrada * policy.breakeven_at_atr
        ):
            stop = max(stop, entrada) if estado > 0 else min(stop, entrada)
            breakeven_aplicado = True

        if (
            policy.trailing_enabled
            and vol_entrada > 0
            and beneficio >= vol_entrada * policy.trailing_activation_atr
            and not np.isnan(v)
        ):
            distancia = v * policy.trailing_atr_multiple
            nuevo = extremo - distancia if estado > 0 else extremo + distancia
            # El stop nunca retrocede: sólo se mueve a favor de la posición.
            stop = max(stop, nuevo) if estado > 0 else min(stop, nuevo)

        efectiva[i] = estado

    return (
        pd.Series(efectiva, index=positions.index),
        pd.Series(motivos, index=positions.index),
    )


def trailing_stop_level(
    entry_price: Decimal,
    extreme_price: Decimal,
    current_atr: Decimal,
    direction: Direction,
    policy: ExitPolicy | None = None,
) -> Decimal:
    """Nivel de stop vigente para una posición abierta.

    Lo usa el panel para mostrar al operador dónde debe estar su stop hoy,
    dado cuánto ha avanzado la posición desde la entrada.

    Args:
        entry_price: Precio al que se abrió.
        extreme_price: Máximo alcanzado en compras, mínimo en ventas.
        current_atr: Volatilidad reciente.
        direction: Dirección de la posición.
        policy: Reglas de salida.

    Returns:
        Nivel de invalidación actualizado.
    """
    cfg = policy or ExitPolicy()
    signo = Decimal(1) if direction is Direction.LONG else Decimal(-1)

    inicial = entry_price - signo * current_atr * Decimal(str(cfg.stop_atr_multiple))
    beneficio = (extreme_price - entry_price) * signo

    nivel = inicial
    if (
        cfg.breakeven_at_atr is not None
        and beneficio >= current_atr * Decimal(str(cfg.breakeven_at_atr))
    ):
        nivel = max(nivel, entry_price) if signo > 0 else min(nivel, entry_price)

    if (
        cfg.trailing_enabled
        and beneficio >= current_atr * Decimal(str(cfg.trailing_activation_atr))
    ):
        arrastrado = extreme_price - signo * current_atr * Decimal(
            str(cfg.trailing_atr_multiple)
        )
        nivel = max(nivel, arrastrado) if signo > 0 else min(nivel, arrastrado)

    return nivel
