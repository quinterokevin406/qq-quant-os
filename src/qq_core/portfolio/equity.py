"""Estado de la cuenta calculado desde el capital propio y el seguimiento.

POR QUÉ ESTE MÓDULO EXISTE
---------------------------
El estado de la cuenta —equity, capital comprometido, riesgo abierto— se puede
calcular sin conectarse a ningún bróker: basta con el capital que el operador
declara y las señales que ha marcado para seguimiento.

Es mejor diseño que depender del gateway MT5, por tres razones:

1. **Funciona hoy**, sin infraestructura adicional.
2. **Mide lo que el sistema recomendó**, no lo que el operador acabó haciendo.
   Para saber si el motor de reparto aporta valor, hay que medir sus propias
   decisiones; mezclarlas con operaciones ajenas al sistema contamina esa
   medición.
3. **No se desincroniza.** Si en la misma cuenta operan otras cosas —un asesor
   experto, operaciones manuales— el equity del bróker deja de corresponder a
   lo que el sistema propuso.

CÓMO SE CALCULA
---------------
    equity = capital_inicial + resultado_cerrado + resultado_flotante

    resultado_cerrado   suma de las señales ya cerradas
    resultado_flotante  suma de las abiertas, a precio de hoy

Y el riesgo, que NO es lo mismo que el capital:

    riesgo_abierto = suma de (distancia al stop x unidades) de lo abierto

Una posición de 10.000 con el stop a un 2% arriesga 200, no 10.000. Confundir
las dos cifras es el error que la especificación pedía expresamente evitar.

LIMITACIÓN DECLARADA
--------------------
El margen real sigue siendo desconocido: depende de las reglas del bróker por
símbolo y volumen. Se calcula una ESTIMACIÓN a partir del apalancamiento
declarado, marcada como tal. No se presenta como dato medido.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class TrackedPosition:
    """Una señal en seguimiento, con lo necesario para valorarla.

    Attributes:
        signal_id: Identificador de la señal.
        symbol: Instrumento.
        strategy: Estrategia que la generó.
        direction: 'long' o 'short'.
        entry_price: Precio de entrada.
        current_price: Precio actual.
        stop_price: Nivel de invalidación. Si falta, el riesgo de esa posición
            no se puede calcular y se declara como desconocido.
        units: Unidades. Si es `None`, se asume el tamaño que recomendó el
            motor de reparto.
        is_closed: Si la operación ya terminó.
        close_price: Precio de cierre, si terminó.
    """

    signal_id: str
    symbol: str
    strategy: str
    direction: str
    entry_price: Decimal
    current_price: Decimal
    stop_price: Decimal | None = None
    units: Decimal = Decimal("1")
    is_closed: bool = False
    close_price: Decimal | None = None

    @property
    def signo(self) -> Decimal:
        """+1 comprado, -1 vendido."""
        return Decimal("1") if self.direction.lower() in ("long", "compra") else Decimal("-1")

    @property
    def pnl(self) -> Decimal:
        """Resultado en divisa de la cuenta, realizado o flotante."""
        salida = (
            self.close_price
            if self.is_closed and self.close_price is not None
            else self.current_price
        )
        return (salida - self.entry_price) * self.signo * self.units

    @property
    def notional(self) -> Decimal:
        """Exposición nocional al precio actual."""
        return self.current_price * self.units

    @property
    def risk_amount(self) -> Decimal | None:
        """Pérdida si se alcanza el stop. `None` si no hay stop declarado.

        `None` significa desconocido y así se declara. Nunca cero: un riesgo
        desconocido presentado como cero haría creer que hay capacidad libre.
        """
        if self.stop_price is None or self.is_closed:
            return None
        return abs(self.entry_price - self.stop_price) * self.units


@dataclass(frozen=True)
class EquityState:
    """Estado de la cuenta derivado del capital propio y el seguimiento.

    Attributes:
        as_of: Momento del cálculo.
        currency: Divisa.
        initial_capital: Capital declarado por el operador.
        realized_pnl: Resultado de las operaciones cerradas.
        floating_pnl: Resultado no realizado de las abiertas.
        equity: Capital inicial más ambos resultados.
        gross_exposure: Nocional total de las posiciones abiertas.
        open_risk: Riesgo agregado de lo abierto, en divisa.
        open_risk_pct: Ese riesgo sobre el equity, en porcentaje.
        positions_without_stop: Posiciones cuyo riesgo no se puede calcular.
        n_open: Posiciones abiertas.
        n_closed: Operaciones cerradas.
        estimated_margin: Margen ESTIMADO desde el apalancamiento declarado.
        leverage: Apalancamiento usado en la estimación.
    """

    as_of: datetime
    currency: str
    initial_capital: Decimal
    realized_pnl: Decimal
    floating_pnl: Decimal
    equity: Decimal
    gross_exposure: Decimal
    open_risk: Decimal
    open_risk_pct: Decimal
    positions_without_stop: tuple[str, ...]
    n_open: int
    n_closed: int
    estimated_margin: Decimal | None
    leverage: int | None

    @property
    def total_return_pct(self) -> Decimal:
        """Rentabilidad acumulada sobre el capital inicial."""
        if self.initial_capital <= 0:
            return Decimal("0")
        return (self.equity - self.initial_capital) / self.initial_capital * Decimal(100)

    @property
    def risk_is_complete(self) -> bool:
        """Cierto si todas las posiciones abiertas tienen stop declarado.

        Si es falso, `open_risk` es una cota INFERIOR del riesgo real: hay
        posiciones cuyo riesgo no se ha podido contar.
        """
        return not self.positions_without_stop

    def to_row(self) -> dict[str, Any]:
        return {
            "Capital inicial": float(self.initial_capital),
            "Equity": float(self.equity),
            "Resultado cerrado": float(self.realized_pnl),
            "Resultado flotante": float(self.floating_pnl),
            "Rentabilidad %": round(float(self.total_return_pct), 2),
            "Exposición nocional": float(self.gross_exposure),
            "Riesgo abierto": float(self.open_risk),
            "Riesgo abierto %": round(float(self.open_risk_pct), 3),
            "Posiciones abiertas": self.n_open,
            "Operaciones cerradas": self.n_closed,
            "Riesgo completo": self.risk_is_complete,
        }


def compute_equity(
    initial_capital: Decimal,
    positions: list[TrackedPosition],
    currency: str = "USD",
    leverage: int | None = None,
) -> EquityState:
    """Calcula el estado de la cuenta sin conectarse a ningún bróker.

    Args:
        initial_capital: Capital que el operador declara haber puesto.
        positions: Señales en seguimiento, abiertas y cerradas.
        currency: Divisa de la cuenta.
        leverage: Apalancamiento del bróker. Si se indica, se estima el margen.

    Returns:
        Estado completo de la cuenta.

    Raises:
        ValueError: Si el capital inicial no es positivo.
    """
    if initial_capital <= 0:
        raise ValueError("el capital inicial debe ser positivo")

    abiertas = [p for p in positions if not p.is_closed]
    cerradas = [p for p in positions if p.is_closed]

    realizado = sum((p.pnl for p in cerradas), Decimal("0"))
    flotante = sum((p.pnl for p in abiertas), Decimal("0"))
    equity = initial_capital + realizado + flotante

    nocional = sum((p.notional for p in abiertas), Decimal("0"))

    riesgo = Decimal("0")
    sin_stop: list[str] = []
    for p in abiertas:
        r = p.risk_amount
        if r is None:
            sin_stop.append(p.signal_id)
        else:
            riesgo += r

    riesgo_pct = riesgo / equity * Decimal(100) if equity > 0 else Decimal("0")

    margen = None
    if leverage and leverage > 0:
        margen = nocional / Decimal(leverage)

    return EquityState(
        as_of=datetime.now(timezone.utc),
        currency=currency,
        initial_capital=initial_capital,
        realized_pnl=realizado,
        floating_pnl=flotante,
        equity=equity,
        gross_exposure=nocional,
        open_risk=riesgo,
        open_risk_pct=riesgo_pct,
        positions_without_stop=tuple(sin_stop),
        n_open=len(abiertas),
        n_closed=len(cerradas),
        estimated_margin=margen,
        leverage=leverage,
    )
