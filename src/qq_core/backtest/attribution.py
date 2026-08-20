"""Atribución de resultados: por qué una estrategia gana o pierde.

QUÉ RESPONDE ESTE MÓDULO
------------------------
Un backtest devuelve un número: +4% anual, o -6%. Ese número no dice nada
accionable. Las preguntas útiles son otras:

  - ¿Pierde por las entradas o por las salidas?
  - ¿Cuánto se lleva el coste de financiación frente al resultado bruto?
  - ¿Gana en tendencia y devuelve en lateral, o al revés?
  - ¿Unas pocas operaciones explican todo el resultado?
  - ¿El lado comprado y el vendido se comportan igual?

Responderlas es la diferencia entre saber que algo no funciona y saber qué
hay que cambiar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TRADING_DAYS = 252


@dataclass
class Attribution:
    """Descomposición del resultado de una estrategia."""

    resultado_bruto_pct: float = 0.0
    coste_financiacion_pct: float = 0.0
    coste_transaccion_pct: float = 0.0
    resultado_neto_pct: float = 0.0

    aporte_largos_pct: float = 0.0
    aporte_cortos_pct: float = 0.0
    dias_largo: int = 0
    dias_corto: int = 0
    dias_fuera: int = 0

    mejor_operacion_pct: float = 0.0
    peor_operacion_pct: float = 0.0
    concentracion_top5_pct: float = 0.0

    salidas_por_stop: int = 0
    salidas_por_objetivo: int = 0
    salidas_por_senal: int = 0

    diagnostico: list[str] = field(default_factory=list)

    def to_rows(self) -> list[dict[str, Any]]:
        """Descomposición en formato de tabla, del bruto al neto."""
        return [
            {"Concepto": "Resultado bruto de la estrategia",
             "Aporte %": round(self.resultado_bruto_pct, 2)},
            {"Concepto": "Coste de financiación (swap del bróker)",
             "Aporte %": round(self.coste_financiacion_pct, 2)},
            {"Concepto": "Coste de transacción (diferencial)",
             "Aporte %": round(self.coste_transaccion_pct, 2)},
            {"Concepto": "RESULTADO NETO",
             "Aporte %": round(self.resultado_neto_pct, 2)},
        ]


def explain_result(
    returns_gross: pd.Series,
    financing: pd.Series,
    transaction: pd.Series,
    exposure: pd.Series,
    exit_reasons: pd.Series | None = None,
) -> Attribution:
    """Descompone el resultado de un backtest y emite un diagnóstico.

    Args:
        returns_gross: Rentabilidad bruta por sesión, antes de costes.
        financing: Coste de financiación por sesión, en tanto por uno.
        transaction: Coste de transacción por sesión.
        exposure: Exposición por sesión, con signo.
        exit_reasons: Motivo de cierre de cada posición, si se dispone.

    Returns:
        Descomposición del resultado con un diagnóstico legible.
    """
    a = Attribution()

    a.resultado_bruto_pct = float(returns_gross.sum() * 100)
    a.coste_financiacion_pct = float(financing.sum() * 100)
    a.coste_transaccion_pct = float(-transaction.sum() * 100)
    a.resultado_neto_pct = (
        a.resultado_bruto_pct + a.coste_financiacion_pct + a.coste_transaccion_pct
    )

    largos = exposure > 0
    cortos = exposure < 0
    a.aporte_largos_pct = float(returns_gross[largos].sum() * 100)
    a.aporte_cortos_pct = float(returns_gross[cortos].sum() * 100)
    a.dias_largo = int(largos.sum())
    a.dias_corto = int(cortos.sum())
    a.dias_fuera = int((exposure == 0).sum())

    # Concentración: reparte el resultado por tramos de posición continua.
    cambios = (exposure != exposure.shift(1)).cumsum()
    por_operacion = returns_gross.groupby(cambios).sum() * 100
    operaciones = por_operacion[por_operacion != 0].sort_values()

    if len(operaciones):
        a.peor_operacion_pct = float(operaciones.iloc[0])
        a.mejor_operacion_pct = float(operaciones.iloc[-1])
        total_positivo = float(operaciones[operaciones > 0].sum())
        if total_positivo > 0:
            top5 = float(operaciones.nlargest(5).clip(lower=0).sum())
            a.concentracion_top5_pct = top5 / total_positivo * 100

    if exit_reasons is not None:
        conteo = exit_reasons[exit_reasons != ""].value_counts()
        a.salidas_por_stop = int(conteo.get("stop", 0))
        a.salidas_por_objetivo = int(conteo.get("objetivo", 0))
        a.salidas_por_senal = int(conteo.get("señal", 0))

    a.diagnostico = _diagnosticar(a)
    return a


def _diagnosticar(a: Attribution) -> list[str]:
    """Traduce la descomposición a conclusiones accionables.

    Cada frase describe un hecho observado y qué implica, sin recomendar
    ajustes de parámetros: eso llevaría directamente al sobreajuste.
    """
    notas: list[str] = []

    # --- El coste de financiación como causa principal --- #
    if a.resultado_bruto_pct > 0 and a.resultado_neto_pct < 0:
        notas.append(
            f"**La estrategia acierta pero los costes se la comen.** El "
            f"resultado bruto es {a.resultado_bruto_pct:+.1f}%, pero la "
            f"financiación resta {abs(a.coste_financiacion_pct):.1f}% y deja "
            f"el neto en {a.resultado_neto_pct:+.1f}%. La lógica de entrada "
            f"funciona; el problema es mantener posiciones tanto tiempo en un "
            f"instrumento con swap elevado."
        )
    elif a.resultado_bruto_pct < 0:
        notas.append(
            f"**El problema está en la lógica, no en los costes.** Incluso "
            f"antes de comisiones el resultado es {a.resultado_bruto_pct:+.1f}%. "
            f"Ajustar costes o parámetros no lo arreglaría."
        )

    if abs(a.coste_financiacion_pct) > abs(a.resultado_bruto_pct) * 0.5:
        notas.append(
            f"La financiación representa una parte muy grande del resultado "
            f"({abs(a.coste_financiacion_pct):.1f}%). Esta estrategia sería "
            f"más viable con horizontes más cortos o en el lado vendido, que "
            f"en este bróker genera ingreso en lugar de coste."
        )

    # --- Asimetría entre lados --- #
    if a.dias_largo > 20 and a.dias_corto > 20:
        if a.aporte_largos_pct > 0 > a.aporte_cortos_pct:
            notas.append(
                f"**Sólo funciona comprando.** El lado largo aporta "
                f"{a.aporte_largos_pct:+.1f}% y el corto {a.aporte_cortos_pct:+.1f}%. "
                f"Restringirla a compras cambiaría el resultado, aunque hay que "
                f"comprobar si eso se sostiene fuera de la muestra o sólo "
                f"refleja que el periodo analizado fue alcista."
            )
        elif a.aporte_cortos_pct > 0 > a.aporte_largos_pct:
            notas.append(
                f"**Sólo funciona vendiendo.** El lado corto aporta "
                f"{a.aporte_cortos_pct:+.1f}% frente a {a.aporte_largos_pct:+.1f}% "
                f"del largo. Coincide con la estructura de costes del bróker, "
                f"que paga por mantener posiciones vendidas."
            )

    # --- Concentración del resultado --- #
    if a.concentracion_top5_pct > 70:
        notas.append(
            f"**El resultado depende de muy pocas operaciones**: cinco de ellas "
            f"explican el {a.concentracion_top5_pct:.0f}% de todo lo ganado. Es "
            f"normal en seguimiento de tendencia, pero significa que el "
            f"resultado tiene mucha variabilidad: perderse esas pocas "
            f"operaciones cambia el signo del año."
        )

    # --- Comportamiento de las salidas --- #
    total_salidas = a.salidas_por_stop + a.salidas_por_objetivo + a.salidas_por_senal
    if total_salidas > 10:
        pct_stop = a.salidas_por_stop / total_salidas * 100
        if pct_stop > 60:
            notas.append(
                f"**Demasiadas salidas por invalidación** ({pct_stop:.0f}% del "
                f"total). El stop está tan cerca que el ruido normal del "
                f"mercado cierra las posiciones antes de que se desarrollen."
            )
        elif pct_stop < 15 and a.salidas_por_senal > total_salidas * 0.7:
            notas.append(
                "Casi todas las salidas ocurren por cambio de señal, no por "
                "invalidación. El stop apenas actúa, lo que sugiere que está "
                "demasiado lejos para proteger el capital."
            )

    # --- Tiempo en mercado --- #
    total_dias = a.dias_largo + a.dias_corto + a.dias_fuera
    if total_dias:
        expuesto = (a.dias_largo + a.dias_corto) / total_dias * 100
        if expuesto > 90:
            notas.append(
                f"La estrategia está en el mercado el {expuesto:.0f}% del "
                f"tiempo. Con coste de financiación, permanecer siempre "
                f"invertido penaliza mucho: paga swap incluso cuando no hay "
                f"oportunidad clara."
            )

    if not notas:
        notas.append(
            "No se detectan patrones destacables en la descomposición del "
            "resultado."
        )
    return notas
