"""Perfil de cuenta y límites de riesgo configurables.

DÓNDE DISCREPO DE LOS VALORES PROPUESTOS EN LA ESPECIFICACIÓN
--------------------------------------------------------------
La especificación pedía expresamente que se analizaran los porcentajes en lugar
de implementarlos tal cual. Estos son los cambios y su motivo.

**Riesgo por operación: 0.25%-1%, excepcional 1.25%.**
Se acepta el rango pero se rebaja el valor por defecto del perfil moderado a
0.50%, no 1%. Motivo: el 1% clásico procede de sistemas con muchas operaciones
independientes. Aquí hay 13 índices bursátiles que se mueven casi al unísono;
cinco posiciones al 1% en índices distintos no son cinco riesgos del 1%, son
aproximadamente un riesgo del 4% con etiquetas diferentes. El límite por
cluster correlacionado mitiga esto, pero el valor base debe reconocerlo.

**Riesgo total abierto máximo: 5%.**
Se acepta. Es coherente con la práctica institucional.

**Riesgo por cluster correlacionado: 2%.**
Se REDUCE a 1.5%. Con trece índices en el universo, el cluster de renta
variable puede absorber casi toda la cartera. Un 2% ahí, con correlaciones que
en crisis tienden a uno, es efectivamente el presupuesto entero concentrado en
una sola apuesta direccional.

**Margen utilizado máximo: 40%.**
Se REDUCE a 25%. Motivo específico de este bróker: el coste de financiación
del lado comprador es del -13.36% anual medido en US500. El margen no es sólo
riesgo de llamada de garantía, es coste corriente. Cada punto de margen usado
en posiciones largas mantenidas semanas cuesta dinero de forma continua. Un
40% de margen en posiciones compradas a varios meses es una sangría garantizada
antes de que el mercado haga nada.

**Reducción de riesgo desde 5% de drawdown, bloqueo en 10%.**
Se ADELANTA: reducción desde el 3%, bloqueo en el 8%. Motivo: la escala de
reducción propuesta en la especificación (0-3% → 1.00, 3-5% → 0.80, etc.) ya
empieza en el 3%, así que la propia especificación se contradice. Se adopta la
escala detallada, que es la coherente.

**Reserva de liquidez 25%.**
Se acepta y se eleva a 30% en el perfil conservador.

LO QUE NO SE PUEDE VALIDAR HOY
-------------------------------
Los límites de margen sólo tienen efecto real cuando el sistema puede leer el
margen del bróker. Con el proveedor manual, el motor los aplica sobre el margen
que el operador introduce. Si no lo introduce, esos límites quedan inactivos y
el panel lo dice explícitamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from decimal import Decimal
from enum import StrEnum
from typing import Any


class RiskProfile(StrEnum):
    """Perfil de riesgo de la cuenta."""

    CONSERVATIVE = "conservador"
    MODERATE = "moderado"
    AGGRESSIVE = "agresivo"


@dataclass(frozen=True)
class RiskLimits:
    """Límites de riesgo de la cartera. Todos configurables.

    Los porcentajes se expresan sobre el equity, en tanto por ciento (1.0 = 1%).

    Attributes:
        base_risk_per_trade_pct: Riesgo objetivo por operación.
        max_risk_per_trade_pct: Techo excepcional por operación.
        max_total_open_risk_pct: Riesgo agregado de todas las posiciones.
        max_risk_per_symbol_pct: Riesgo acumulado en un mismo instrumento.
        max_risk_per_strategy_pct: Riesgo acumulado en una misma estrategia.
        max_risk_per_cluster_pct: Riesgo en un grupo de instrumentos que se
            mueven juntos. Es el límite que más protege en esta cartera.
        max_margin_pct: Margen utilizado máximo sobre el equity.
        min_cash_reserve_pct: Liquidez que debe permanecer libre.
        max_gross_exposure: Exposición nocional total como múltiplo del equity.
        max_positions: Posiciones abiertas simultáneas.
        max_directional_net_pct: Sesgo direccional neto máximo. Impide que toda
            la cartera esté comprada o vendida a la vez.
        drawdown_reduce_from_pct: Caída desde la que empieza a reducirse riesgo.
        drawdown_block_at_pct: Caída a la que se bloquean nuevas posiciones.
        min_trades_for_scoring: Operaciones mínimas en una casilla para que su
            estadística se considere utilizable. Ver `quality.py`.
    """

    base_risk_per_trade_pct: Decimal = Decimal("0.50")
    max_risk_per_trade_pct: Decimal = Decimal("1.00")
    max_total_open_risk_pct: Decimal = Decimal("5.00")
    max_risk_per_symbol_pct: Decimal = Decimal("1.50")
    max_risk_per_strategy_pct: Decimal = Decimal("2.00")
    max_risk_per_cluster_pct: Decimal = Decimal("1.50")
    max_margin_pct: Decimal = Decimal("25.00")
    min_cash_reserve_pct: Decimal = Decimal("25.00")
    max_gross_exposure: Decimal = Decimal("1.00")
    max_positions: int = 8
    max_directional_net_pct: Decimal = Decimal("60.00")
    drawdown_reduce_from_pct: Decimal = Decimal("3.00")
    drawdown_block_at_pct: Decimal = Decimal("8.00")
    min_trades_for_scoring: int = 30

    def validated(self) -> RiskLimits:
        """Comprueba la coherencia interna de los límites.

        Raises:
            ValueError: Si los límites se contradicen entre sí. Un conjunto
                incoherente es peor que uno malo: produce comportamiento
                impredecible en lugar de un error visible.
        """
        if self.base_risk_per_trade_pct > self.max_risk_per_trade_pct:
            raise ValueError("el riesgo base no puede superar el techo por operación")
        if self.max_risk_per_trade_pct > self.max_total_open_risk_pct:
            raise ValueError("una sola operación no puede agotar el riesgo total")
        if self.max_risk_per_cluster_pct > self.max_total_open_risk_pct:
            raise ValueError("un cluster no puede superar el riesgo total")
        if self.drawdown_reduce_from_pct >= self.drawdown_block_at_pct:
            raise ValueError("la reducción debe empezar antes del bloqueo")
        if self.min_cash_reserve_pct + self.max_margin_pct > Decimal(100):
            raise ValueError("reserva de liquidez y margen máximo suman más del 100%")
        if self.max_positions < 1:
            raise ValueError("debe permitirse al menos una posición")
        return self


PERFILES: dict[RiskProfile, RiskLimits] = {
    RiskProfile.CONSERVATIVE: RiskLimits(
        base_risk_per_trade_pct=Decimal("0.25"),
        max_risk_per_trade_pct=Decimal("0.50"),
        max_total_open_risk_pct=Decimal("3.00"),
        max_risk_per_symbol_pct=Decimal("0.75"),
        max_risk_per_strategy_pct=Decimal("1.00"),
        max_risk_per_cluster_pct=Decimal("1.00"),
        max_margin_pct=Decimal("15.00"),
        min_cash_reserve_pct=Decimal("30.00"),
        max_gross_exposure=Decimal("0.75"),
        max_positions=5,
        max_directional_net_pct=Decimal("40.00"),
        drawdown_reduce_from_pct=Decimal("2.00"),
        drawdown_block_at_pct=Decimal("6.00"),
    ),
    RiskProfile.MODERATE: RiskLimits(),
    RiskProfile.AGGRESSIVE: RiskLimits(
        base_risk_per_trade_pct=Decimal("0.75"),
        max_risk_per_trade_pct=Decimal("1.25"),
        max_total_open_risk_pct=Decimal("7.00"),
        max_risk_per_symbol_pct=Decimal("2.00"),
        max_risk_per_strategy_pct=Decimal("3.00"),
        max_risk_per_cluster_pct=Decimal("2.50"),
        max_margin_pct=Decimal("35.00"),
        min_cash_reserve_pct=Decimal("20.00"),
        max_gross_exposure=Decimal("1.50"),
        max_positions=12,
        max_directional_net_pct=Decimal("75.00"),
        drawdown_reduce_from_pct=Decimal("4.00"),
        drawdown_block_at_pct=Decimal("10.00"),
    ),
}


DRAWDOWN_ESCALA: tuple[tuple[Decimal, Decimal], ...] = (
    (Decimal("3.0"), Decimal("1.00")),
    (Decimal("5.0"), Decimal("0.80")),
    (Decimal("7.5"), Decimal("0.60")),
    (Decimal("10.0"), Decimal("0.35")),
)
"""Multiplicador de riesgo según la caída acumulada.

Escala de la especificación, adoptada tal cual. Su lógica es correcta: reducir
el tamaño cuando el sistema está perdiendo evita que una racha mala se
convierta en irreversible. El bloqueo total lo decide `drawdown_block_at_pct`,
que puede ser más estricto que el último tramo de esta escala.
"""


def drawdown_multiplier(drawdown_pct: Decimal, limits: RiskLimits) -> Decimal:
    """Multiplicador de riesgo correspondiente a una caída dada.

    Args:
        drawdown_pct: Caída actual desde máximos, en positivo (2.5 = -2.5%).
        limits: Límites vigentes.

    Returns:
        Factor entre 0 y 1. Cero significa bloqueo de nuevas posiciones.
    """
    dd = abs(Decimal(drawdown_pct))

    if dd >= limits.drawdown_block_at_pct:
        return Decimal("0")

    for umbral, factor in DRAWDOWN_ESCALA:
        if dd < umbral:
            return factor

    return Decimal("0")


@dataclass(frozen=True)
class AccountProfile:
    """Configuración completa de la cuenta.

    Attributes:
        name: Nombre identificativo.
        currency: Divisa.
        initial_capital: Capital aportado inicialmente. Referencia para el
            resultado acumulado.
        profile: Perfil de riesgo elegido.
        limits: Límites vigentes. Parten del perfil pero pueden modificarse uno
            a uno.
        peak_equity: Máximo histórico de equity. Base para el cálculo de la
            caída. Si es `None`, se toma el equity actual.
        notes: Anotaciones del operador.
    """

    name: str = "principal"
    currency: str = "USD"
    initial_capital: Decimal = Decimal("10000")
    profile: RiskProfile = RiskProfile.MODERATE
    limits: RiskLimits = field(default_factory=RiskLimits)
    peak_equity: Decimal | None = None
    notes: str = ""

    @classmethod
    def from_profile(
        cls,
        profile: RiskProfile,
        initial_capital: Decimal,
        currency: str = "USD",
        name: str = "principal",
    ) -> AccountProfile:
        """Crea un perfil con los límites por defecto de un perfil de riesgo."""
        return cls(
            name=name,
            currency=currency,
            initial_capital=Decimal(initial_capital),
            profile=profile,
            limits=PERFILES[profile].validated(),
        )

    def with_limits(self, **cambios: Any) -> AccountProfile:
        """Devuelve una copia con los límites indicados modificados.

        Args:
            **cambios: Campos de `RiskLimits` a sustituir.

        Returns:
            Perfil nuevo con los límites validados.

        Raises:
            ValueError: Si los límites resultantes son incoherentes.
        """
        nuevos = replace(self.limits, **cambios).validated()
        return replace(self, limits=nuevos)

    def current_drawdown_pct(self, equity: Decimal) -> Decimal:
        """Caída actual desde el máximo histórico, en positivo.

        Args:
            equity: Equity actual.

        Returns:
            Porcentaje de caída. Cero si se está en máximos.
        """
        pico = self.peak_equity if self.peak_equity is not None else equity
        pico = max(pico, equity)
        if pico <= 0:
            return Decimal("0")
        return max(Decimal("0"), (pico - equity) / pico * Decimal(100))

    def to_row(self) -> dict[str, Any]:
        """Resumen para el panel."""
        lim = self.limits
        return {
            "Cuenta": self.name,
            "Divisa": self.currency,
            "Capital inicial": float(self.initial_capital),
            "Perfil": self.profile.value,
            "Riesgo base por operación %": float(lim.base_risk_per_trade_pct),
            "Riesgo total máximo %": float(lim.max_total_open_risk_pct),
            "Riesgo por cluster %": float(lim.max_risk_per_cluster_pct),
            "Margen máximo %": float(lim.max_margin_pct),
            "Reserva de liquidez %": float(lim.min_cash_reserve_pct),
            "Posiciones máximas": lim.max_positions,
            "Bloqueo por caída %": float(lim.drawdown_block_at_pct),
        }
