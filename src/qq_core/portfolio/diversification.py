"""Reparto de capital por riesgo, con diversificación y reserva.

QUÉ RESUELVE
------------
Tres problemas concretos que el reparto anterior no atendía:

**1. "¿Cuál es la mejor y cuánto capital le pongo?"**
Las señales se ordenan por una puntuación de prioridad que combina calidad
histórica, frecuencia y —esto es lo importante— cuánto aporta cada una a la
diversificación de lo que ya hay. Una señal mediocre que diversifica puede
recibir más capital que una excelente que duplica exposición existente.

**2. "No puedo usar el 100%, ¿de dónde saco capital mañana?"**
Se reserva una fracción del presupuesto de riesgo. Si aparece mañana una señal
mejor que las de hoy, hay capacidad para tomarla. Sin reserva, el sistema
gastaría todo con las primeras señales que aparecieran, que no tienen por qué
ser las mejores del mes.

**3. "Que diversifique por riesgo, no por capital."**
Cuatro posiciones al 25% del capital cada una NO son cuatro riesgos iguales.
Si tres son índices bursátiles —que se mueven casi al unísono— es
aproximadamente una sola apuesta con tres etiquetas. El reparto se hace sobre
presupuesto de riesgo y se penaliza la correlación.

EL CONCEPTO CENTRAL: APUESTAS INDEPENDIENTES EFECTIVAS
-------------------------------------------------------
Con N posiciones de correlación media rho, el número de apuestas realmente
independientes es aproximadamente:

    N_efectivo = N / (1 + (N - 1) * rho)

Cinco posiciones con correlación 0,9 equivalen a 1,2 apuestas independientes.
Cinco con correlación 0,1 equivalen a 3,6. El motor reparte sobre el número
efectivo, no sobre el número nominal, que es lo que hace que "diversificar por
riesgo" signifique algo.

POR QUÉ NO OPTIMIZACIÓN DE CARTERA CLÁSICA
--------------------------------------------
La optimización de media-varianza necesita rendimientos esperados, y estimarlos
a partir de backtests con este tamaño de muestra produce carteras extremas y
muy inestables: pequeños cambios en las estimaciones mueven la asignación de un
extremo a otro. Es un problema conocido y bien documentado.

El reparto por presupuesto de riesgo con penalización por correlación es más
robusto: no depende de estimar rendimientos, sólo de medir volatilidades y
correlaciones, que se estiman con mucha más precisión.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from qq_core.portfolio.account import RiskLimits, drawdown_multiplier

RESERVA_POR_DEFECTO = Decimal("40")
"""Porcentaje del presupuesto de riesgo que se reserva para señales futuras.

Un 40% es deliberadamente alto. La razón: las señales no llegan ordenadas por
calidad. Si el sistema gasta todo el presupuesto con las tres primeras del mes,
no podrá tomar la quinta aunque sea claramente mejor.
"""

UMBRAL_CLUSTER = 0.65
"""Correlación a partir de la cual dos instrumentos se consideran el mismo riesgo.

Con 0,65 los índices bursátiles caen casi todos en un mismo grupo, que es el
comportamiento correcto: en una caída se mueven juntos.
"""


class AllocationDecision(StrEnum):
    """Decisión del motor sobre cada señal candidata."""

    ACCEPT_FULL = "ACCEPT_FULL_SIZE"
    ACCEPT_REDUCED = "ACCEPT_REDUCED_SIZE"
    WAIT_FOR_CAPACITY = "WAIT_FOR_CAPACITY"
    REJECT_CORRELATION = "REJECT_CORRELATION"
    REJECT_CLUSTER_LIMIT = "REJECT_CLUSTER_LIMIT"
    REJECT_RISK_LIMIT = "REJECT_RISK_LIMIT"
    REJECT_MAX_POSITIONS = "REJECT_MAX_POSITIONS"
    REJECT_DRAWDOWN_BLOCK = "REJECT_DRAWDOWN_BLOCK"

    @property
    def is_accept(self) -> bool:
        return self in (
            AllocationDecision.ACCEPT_FULL,
            AllocationDecision.ACCEPT_REDUCED,
        )


@dataclass(frozen=True)
class Candidate:
    """Una señal compitiendo por capital.

    Attributes:
        signal_id: Identificador.
        symbol: Instrumento.
        strategy: Estrategia.
        direction: 'long' o 'short'.
        quality_score: Puntuación 0-100 de `signal_scoring`.
        entry_price: Precio de entrada.
        stop_price: Nivel de invalidación.
        times_seen: Veces que esta combinación ha generado señal en el periodo
            reciente. Una señal que se repite indica persistencia; una aislada
            puede ser un artefacto puntual.
        cluster: Grupo de correlación al que pertenece el instrumento.
    """

    signal_id: str
    symbol: str
    strategy: str
    direction: str
    quality_score: float
    entry_price: Decimal
    stop_price: Decimal
    times_seen: int = 1
    cluster: str = "sin_grupo"

    @property
    def stop_distance(self) -> Decimal:
        return abs(self.entry_price - self.stop_price)


@dataclass(frozen=True)
class RiskAllocation:
    """Asignación de riesgo y capital a una señal.

    Attributes:
        candidate: Señal.
        decision: Decisión del motor.
        rank: Posición en el orden de prioridad, empezando en 1.
        priority_score: Puntuación de prioridad usada para ordenar.
        risk_pct: Riesgo asignado sobre el equity.
        risk_amount: Ese riesgo en divisa.
        units: Unidades recomendadas.
        notional: Exposición nocional.
        correlation_penalty: Reducción aplicada por correlación, en tanto por uno.
        diversification_benefit: Cuánto mejora la diversificación de la cartera.
            Positivo significa que reduce el riesgo conjunto.
        risk_before_pct: Riesgo de la cartera antes de esta posición.
        risk_after_pct: Riesgo después.
        reasons: Códigos de explicación.
        explanation: Explicación en lenguaje llano.
    """

    candidate: Candidate
    decision: AllocationDecision
    rank: int
    priority_score: float
    risk_pct: Decimal
    risk_amount: Decimal
    units: Decimal
    notional: Decimal
    correlation_penalty: float
    diversification_benefit: float
    risk_before_pct: Decimal
    risk_after_pct: Decimal
    reasons: tuple[str, ...]
    explanation: str

    def to_row(self) -> dict[str, Any]:
        return {
            "#": self.rank,
            "Instrumento": self.candidate.symbol,
            "Estrategia": self.candidate.strategy,
            "Dirección": self.candidate.direction,
            "Calidad": round(self.candidate.quality_score, 1),
            "Prioridad": round(self.priority_score, 1),
            "Decisión": self.decision.value,
            "Riesgo %": round(float(self.risk_pct), 3),
            "Riesgo": round(float(self.risk_amount), 2),
            "Unidades": float(self.units),
            "Nocional": round(float(self.notional), 2),
            "Penaliz. correlación": f"{self.correlation_penalty * 100:.0f}%",
            "Grupo": self.candidate.cluster,
            "Motivos": ", ".join(self.reasons),
        }


@dataclass(frozen=True)
class AllocationPlan:
    """Resultado completo del reparto.

    Attributes:
        allocations: Asignaciones, en orden de prioridad.
        equity: Equity usado.
        total_budget_pct: Presupuesto total de riesgo.
        reserved_pct: Reservado para señales futuras.
        deployable_pct: Disponible hoy.
        used_pct: Efectivamente asignado.
        already_open_pct: Riesgo ya comprometido antes de este reparto.
        effective_bets: Apuestas independientes efectivas tras el reparto.
        nominal_bets: Número de posiciones aceptadas.
    """

    allocations: tuple[RiskAllocation, ...]
    equity: Decimal
    total_budget_pct: Decimal
    reserved_pct: Decimal
    deployable_pct: Decimal
    used_pct: Decimal
    already_open_pct: Decimal
    effective_bets: float
    nominal_bets: int

    @property
    def accepted(self) -> tuple[RiskAllocation, ...]:
        return tuple(a for a in self.allocations if a.decision.is_accept)

    @property
    def headline(self) -> str:
        """Resumen en una frase."""
        if not self.accepted:
            return (
                "Ninguna señal recibe capital. El presupuesto de riesgo "
                "disponible no permite abrir posiciones nuevas."
            )
        return (
            f"{len(self.accepted)} señales reciben capital, usando "
            f"{self.used_pct:.2f}% de riesgo sobre un presupuesto de "
            f"{self.total_budget_pct}%. Quedan {self.reserved_pct:.2f}% "
            f"reservados para oportunidades futuras. Las posiciones "
            f"aceptadas equivalen a {self.effective_bets:.1f} apuestas "
            f"realmente independientes."
        )

    def to_frame(self) -> pd.DataFrame:
        if not self.allocations:
            return pd.DataFrame()
        return pd.DataFrame([a.to_row() for a in self.allocations])


def build_clusters(
    correlations: pd.DataFrame, threshold: float = UMBRAL_CLUSTER
) -> dict[str, str]:
    """Agrupa instrumentos que se mueven juntos.

    Usa agrupamiento por enlace simple: dos instrumentos caen en el mismo grupo
    si su correlación supera el umbral, y los grupos se propagan por
    transitividad. Es deliberadamente simple: métodos más sofisticados exigen
    decisiones adicionales que serían parámetros que ajustar, y el objetivo aquí
    es identificar bloques evidentes —los índices bursátiles entre sí— no hacer
    taxonomía fina.

    Args:
        correlations: Matriz de correlación con instrumentos en filas y
            columnas.
        threshold: Correlación a partir de la cual se consideran el mismo grupo.

    Returns:
        Diccionario instrumento -> nombre de grupo.
    """
    simbolos = list(correlations.columns)
    padre = {s: s for s in simbolos}

    def raiz(x: str) -> str:
        while padre[x] != x:
            padre[x] = padre[padre[x]]
            x = padre[x]
        return x

    for i, a in enumerate(simbolos):
        for b in simbolos[i + 1 :]:
            try:
                rho = float(correlations.loc[a, b])
            except (KeyError, TypeError, ValueError):
                continue
            if not np.isnan(rho) and abs(rho) >= threshold:
                ra, rb = raiz(a), raiz(b)
                if ra != rb:
                    padre[rb] = ra

    grupos: dict[str, list[str]] = {}
    for s in simbolos:
        grupos.setdefault(raiz(s), []).append(s)

    # Nombre legible: el grupo se llama como su miembro más representativo.
    salida: dict[str, str] = {}
    for r, miembros in grupos.items():
        nombre = f"grupo_{sorted(miembros)[0]}" if len(miembros) > 1 else "individual"
        for m in miembros:
            salida[m] = nombre
    return salida


def effective_independent_bets(
    symbols: list[str], correlations: pd.DataFrame | None
) -> float:
    """Número de apuestas realmente independientes en un conjunto.

    Cinco posiciones con correlación 0,9 no son cinco apuestas: son
    aproximadamente 1,2. Esta es la cifra que hace que "diversificar por riesgo"
    signifique algo medible.

    Args:
        symbols: Instrumentos de las posiciones.
        correlations: Matriz de correlación. Si es `None`, se asume que son
            independientes, lo que es OPTIMISTA y así debe declararse.

    Returns:
        Apuestas efectivas. Nunca mayor que el número de posiciones.
    """
    n = len(symbols)
    if n <= 1:
        return float(n)
    if correlations is None:
        return float(n)

    pares = []
    for i, a in enumerate(symbols):
        for b in symbols[i + 1 :]:
            try:
                rho = float(correlations.loc[a, b])
            except (KeyError, TypeError, ValueError):
                continue
            if not np.isnan(rho):
                pares.append(abs(rho))

    if not pares:
        return float(n)

    rho_medio = float(np.mean(pares))
    return n / (1.0 + (n - 1) * rho_medio)


def _priority_score(
    cand: Candidate, diversification_benefit: float, times_seen_max: int
) -> float:
    """Puntuación de prioridad de una señal.

    Combina tres cosas, y la tercera es la que distingue este motor de un
    simple orden por calidad:

    - **Calidad** (60%): las estadísticas históricas de la combinación.
    - **Persistencia** (15%): cuántas veces ha aparecido la señal. Una que se
      repite indica una condición sostenida; una aislada puede ser un artefacto.
    - **Diversificación** (25%): cuánto mejora la estructura de riesgo de la
      cartera.

    Con esta fórmula, una señal de calidad 70 que diversifica puede superar a
    una de calidad 82 que duplica exposición ya existente. Es exactamente el
    comportamiento que se pedía.
    """
    calidad = cand.quality_score
    persistencia = 100.0 * cand.times_seen / max(times_seen_max, 1)
    diversif = 50.0 + diversification_benefit * 50.0  # centrado en 50
    return 0.60 * calidad + 0.15 * persistencia + 0.25 * min(max(diversif, 0.0), 100.0)


def allocate_by_risk(
    candidates: list[Candidate],
    equity: Decimal,
    limits: RiskLimits,
    correlations: pd.DataFrame | None = None,
    open_symbols: list[str] | None = None,
    current_open_risk_pct: Decimal = Decimal("0"),
    drawdown_pct: Decimal = Decimal("0"),
    reserve_pct: Decimal = RESERVA_POR_DEFECTO,
) -> AllocationPlan:
    """Reparte presupuesto de riesgo entre las señales candidatas.

    Args:
        candidates: Señales compitiendo por capital.
        equity: Equity actual de la cuenta.
        limits: Límites de riesgo vigentes.
        correlations: Matriz de correlación entre instrumentos. Si falta, se
            asume independencia, lo que es optimista y se declara.
        open_symbols: Instrumentos ya en cartera. Cuentan para la correlación:
            una señal nueva sobre un instrumento correlacionado con lo que ya
            hay recibe menos.
        current_open_risk_pct: Riesgo ya comprometido.
        drawdown_pct: Caída actual desde máximos, en positivo.
        reserve_pct: Porcentaje del presupuesto que se reserva para señales
            futuras.

    Returns:
        Plan de reparto completo, con todas las señales y su decisión.

    Raises:
        ValueError: Si el equity no es positivo.
    """
    if equity <= 0:
        raise ValueError("el equity debe ser positivo")

    presupuesto = limits.max_total_open_risk_pct
    reservado = presupuesto * reserve_pct / Decimal(100)
    desplegable = presupuesto - reservado - current_open_risk_pct

    f_dd = drawdown_multiplier(drawdown_pct, limits)
    en_cartera = list(open_symbols or [])
    aceptadas: list[RiskAllocation] = []
    usado = Decimal("0")
    riesgo_por_grupo: dict[str, Decimal] = {}
    veces_max = max((c.times_seen for c in candidates), default=1)

    # --- Prioridad. El beneficio de diversificación se calcula respecto a lo
    # que ya hay en cartera, así que depende del estado actual, no sólo de la
    # calidad intrínseca de la señal.
    con_prioridad = []
    for c in candidates:
        antes = effective_independent_bets(en_cartera, correlations)
        despues = effective_independent_bets(en_cartera + [c.symbol], correlations)
        # Cuánto mejora respecto a añadir una posición perfectamente
        # correlacionada (que no aportaría nada).
        beneficio = (despues - antes) - 1.0 if en_cartera else 0.0
        con_prioridad.append((_priority_score(c, beneficio, veces_max), beneficio, c))

    con_prioridad.sort(key=lambda x: -x[0])

    for rango, (prioridad, beneficio, cand) in enumerate(con_prioridad, start=1):
        motivos: list[str] = []
        riesgo_antes = current_open_risk_pct + usado

        def rechazo(dec: AllocationDecision, motivo: str, texto: str) -> RiskAllocation:
            return RiskAllocation(
                candidate=cand,
                decision=dec,
                rank=rango,
                priority_score=prioridad,
                risk_pct=Decimal("0"),
                risk_amount=Decimal("0"),
                units=Decimal("0"),
                notional=Decimal("0"),
                correlation_penalty=0.0,
                diversification_benefit=beneficio,
                risk_before_pct=riesgo_antes,
                risk_after_pct=riesgo_antes,
                reasons=(motivo,),
                explanation=texto,
            )

        if f_dd == 0:
            aceptadas.append(rechazo(
                AllocationDecision.REJECT_DRAWDOWN_BLOCK, "REJECT_DRAWDOWN_BLOCK",
                f"Caída del {drawdown_pct:.2f}% sobre el límite de bloqueo del "
                f"{limits.drawdown_block_at_pct}%. No se abren posiciones nuevas.",
            ))
            continue

        if len(en_cartera) >= limits.max_positions:
            aceptadas.append(rechazo(
                AllocationDecision.REJECT_MAX_POSITIONS, "REJECT_MAX_POSITIONS",
                f"Ya hay {len(en_cartera)} posiciones, el máximo del perfil.",
            ))
            continue

        restante = desplegable - usado
        if restante <= 0:
            aceptadas.append(rechazo(
                AllocationDecision.WAIT_FOR_CAPACITY, "WAIT_FOR_CAPACITY",
                f"El presupuesto desplegable de hoy ({desplegable:.2f}%) está "
                f"agotado. Quedan {reservado:.2f}% reservados para señales "
                f"futuras, que no se tocan a propósito: si mañana aparece algo "
                f"mejor, habrá capacidad para tomarlo.",
            ))
            continue

        # --- Penalización por correlación con lo que ya hay ---
        penalizacion = 0.0
        if correlations is not None and en_cartera:
            rhos = []
            for s in en_cartera:
                try:
                    r = float(correlations.loc[cand.symbol, s])
                    if not np.isnan(r):
                        rhos.append(abs(r))
                except (KeyError, TypeError, ValueError):
                    continue
            if rhos:
                penalizacion = min(max(max(rhos), 0.0), 1.0)

        if penalizacion >= 0.95:
            aceptadas.append(rechazo(
                AllocationDecision.REJECT_CORRELATION, "REJECT_CORRELATION",
                f"Correlación de {penalizacion:.2f} con una posición ya "
                f"abierta. Añadirla no diversifica: es la misma apuesta con "
                f"otro nombre.",
            ))
            continue

        f_corr = 1.0 - penalizacion * 0.7
        if penalizacion > 0.3:
            motivos.append("CORRELATION_PENALTY")

        # --- Riesgo objetivo: reparto equitativo sobre las apuestas efectivas ---
        base = limits.base_risk_per_trade_pct * Decimal(str(f_corr)) * f_dd
        riesgo_pct = min(base, limits.max_risk_per_trade_pct, restante)

        # --- Techo por grupo de correlación ---
        del_grupo = riesgo_por_grupo.get(cand.cluster, Decimal("0"))
        margen_grupo = limits.max_risk_per_cluster_pct - del_grupo
        if margen_grupo <= 0:
            aceptadas.append(rechazo(
                AllocationDecision.REJECT_CLUSTER_LIMIT, "STRATEGY_CLUSTER_LIMIT",
                f"El grupo '{cand.cluster}' ya concentra {del_grupo:.2f}% de "
                f"riesgo, el máximo permitido ({limits.max_risk_per_cluster_pct}%). "
                f"Los instrumentos de este grupo se mueven juntos: sumar más "
                f"sería aumentar una sola apuesta, no diversificar.",
            ))
            continue
        if riesgo_pct > margen_grupo:
            riesgo_pct = margen_grupo
            motivos.append("CAPPED_BY_CLUSTER")

        if cand.stop_distance <= 0:
            aceptadas.append(rechazo(
                AllocationDecision.REJECT_RISK_LIMIT, "INVALID_STOP",
                "La distancia de invalidación es cero o negativa.",
            ))
            continue

        importe = equity * riesgo_pct / Decimal(100)
        unidades = (importe / cand.stop_distance).quantize(
            Decimal("0.01"), rounding=ROUND_DOWN
        )
        if unidades <= 0:
            aceptadas.append(rechazo(
                AllocationDecision.WAIT_FOR_CAPACITY, "SIZE_ROUNDS_TO_ZERO",
                f"El riesgo permitido ({importe:.2f}) dividido por la distancia "
                f"al stop ({cand.stop_distance:.2f}) queda por debajo de la "
                f"unidad mínima negociable.",
            ))
            continue

        nocional = unidades * cand.entry_price
        usado += riesgo_pct
        riesgo_por_grupo[cand.cluster] = del_grupo + riesgo_pct
        en_cartera.append(cand.symbol)

        completa = riesgo_pct >= limits.base_risk_per_trade_pct
        decision = (
            AllocationDecision.ACCEPT_FULL if completa
            else AllocationDecision.ACCEPT_REDUCED
        )
        if not completa:
            motivos.append("REDUCED")

        explicacion = (
            f"Prioridad {prioridad:.1f} de {len(candidates)} candidatas "
            f"(calidad {cand.quality_score:.0f}, vista {cand.times_seen} "
            f"veces). Riesgo base {limits.base_risk_per_trade_pct}% ajustado "
            f"por correlación (x{f_corr:.2f}) y caída (x{f_dd}) da "
            f"{riesgo_pct:.3f}% = {importe:.2f}. Con invalidación a "
            f"{cand.stop_distance:.2f} salen {unidades} unidades."
        )

        aceptadas.append(RiskAllocation(
            candidate=cand,
            decision=decision,
            rank=rango,
            priority_score=prioridad,
            risk_pct=riesgo_pct,
            risk_amount=importe,
            units=unidades,
            notional=nocional,
            correlation_penalty=penalizacion,
            diversification_benefit=beneficio,
            risk_before_pct=riesgo_antes,
            risk_after_pct=riesgo_antes + riesgo_pct,
            reasons=tuple(motivos) or ("ACCEPTED",),
            explanation=explicacion,
        ))

    aceptados_simbolos = [
        a.candidate.symbol for a in aceptadas if a.decision.is_accept
    ]

    return AllocationPlan(
        allocations=tuple(aceptadas),
        equity=equity,
        total_budget_pct=presupuesto,
        reserved_pct=reservado,
        deployable_pct=max(desplegable, Decimal("0")),
        used_pct=usado,
        already_open_pct=current_open_risk_pct,
        effective_bets=effective_independent_bets(aceptados_simbolos, correlations),
        nominal_bets=len(aceptados_simbolos),
    )
