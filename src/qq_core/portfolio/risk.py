"""Motor de riesgo: dimensionamiento de posiciones (Módulo 09).

EL PROBLEMA QUE RESUELVE
------------------------
Una señal dice "comprar US500". No dice cuánto. Esa segunda pregunta determina
el resultado más que la primera: se puede acertar la dirección en el 60% de las
operaciones y aun así arruinar la cuenta si el tamaño está mal calculado.

PRINCIPIO: SE DIMENSIONA POR RIESGO, NO POR CAPITAL
----------------------------------------------------
El error habitual es repartir el capital a partes iguales: 10.000 dólares a
cada posición. Eso asigna el mismo dinero pero riesgos muy distintos, porque
el petróleo se mueve tres veces más que un índice. El resultado es una cartera
donde dos o tres posiciones volátiles dominan todo el resultado.

Aquí se hace al revés: se fija cuánto se está dispuesto a perder en cada
operación —un porcentaje del capital— y el tamaño sale de dividir ese importe
entre la distancia al nivel de invalidación. Una posición con stop lejano
recibe menos unidades; una con stop cercano, más. Todas arriesgan lo mismo.

RESTRICCIÓN ADICIONAL: EL MARGEN NO DIMENSIONA
-----------------------------------------------
El bróker permite apalancamiento 100:1 (ADR-0003). Dimensionar según el margen
disponible permitiría abrir exposición absurda respecto al capital. El margen
se trata como una restricción que no debe alcanzarse nunca, no como una guía.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from qq_core.domain.signal import Direction, Signal


@dataclass(frozen=True)
class RiskConfig:
    """Parámetros de gestión de riesgo de la cartera.

    Attributes:
        risk_per_trade_pct: Porcentaje del capital que se arriesga en cada
            operación, entendido como la pérdida si se alcanza el nivel de
            invalidación. 1% es el estándar conservador de la industria.
        max_position_weight: Peso nocional máximo de una posición sobre el
            capital. Impide que un stop muy cercano produzca una posición
            enorme aunque el riesgo calculado sea correcto.
        max_gross_exposure: Suma de exposiciones nocionales permitida, como
            múltiplo del capital. 1.0 significa sin apalancamiento agregado.
        max_positions: Número máximo de posiciones abiertas simultáneas.
            Limita la dispersión y hace la cartera manejable manualmente.
        stop_atr_multiple: Distancia del nivel de invalidación en múltiplos de
            volatilidad reciente.
    """

    risk_per_trade_pct: Decimal = Decimal("1.0")
    max_position_weight: Decimal = Decimal("0.35")
    max_gross_exposure: Decimal = Decimal("1.0")
    max_positions: int = 8
    stop_atr_multiple: Decimal = Decimal("2.0")


@dataclass
class Position:
    """Posición abierta en la cartera.

    Attributes:
        symbol: Instrumento.
        direction: Comprada o vendida.
        units: Unidades del instrumento.
        entry_price: Precio de entrada.
        entry_date: Fecha de apertura.
        stop_price: Nivel de invalidación.
        strategy: Estrategia que la originó.
        current_price: Último precio conocido.
    """

    symbol: str
    direction: Direction
    units: Decimal
    entry_price: Decimal
    entry_date: date
    stop_price: Decimal | None
    strategy: str
    current_price: Decimal | None = None

    @property
    def notional(self) -> Decimal:
        """Valor nocional actual de la posición."""
        precio = self.current_price or self.entry_price
        return abs(self.units) * precio

    @property
    def unrealized_pnl(self) -> Decimal:
        """Resultado no realizado en divisa de cuenta."""
        if self.current_price is None:
            return Decimal(0)
        movimiento = self.current_price - self.entry_price
        if self.direction is Direction.SHORT:
            movimiento = -movimiento
        return movimiento * abs(self.units)

    @property
    def return_pct(self) -> Decimal:
        """Rentabilidad de la posición sobre el capital comprometido."""
        base = abs(self.units) * self.entry_price
        if base == 0:
            return Decimal(0)
        return (self.unrealized_pnl / base) * 100

    @property
    def days_held(self) -> int:
        return (date.today() - self.entry_date).days

    @property
    def risk_at_stop(self) -> Decimal:
        """Pérdida si se alcanza el nivel de invalidación."""
        if self.stop_price is None:
            return Decimal(0)
        distancia = abs(self.entry_price - self.stop_price)
        return distancia * abs(self.units)


@dataclass
class Order:
    """Orden concreta a ejecutar manualmente en el bróker.

    Es la salida final del sistema para el operador: qué instrumento, en qué
    dirección, cuántas unidades, a qué precio de referencia y con qué nivel de
    invalidación. Todo lo demás de la plataforma existe para producir esto.
    """

    action: str
    symbol: str
    direction: Direction
    units: Decimal
    reference_price: Decimal
    stop_price: Decimal | None
    risk_amount: Decimal
    notional: Decimal
    strategy: str
    rationale: str

    def to_row(self) -> dict:
        return {
            "Acción": self.action,
            "Instrumento": self.symbol,
            "Dirección": "COMPRA" if self.direction is Direction.LONG else "VENTA",
            "Unidades": float(round(self.units, 4)),
            "Precio ref.": float(round(self.reference_price, 4)),
            "Invalidación": (
                float(round(self.stop_price, 4)) if self.stop_price else None
            ),
            "Riesgo $": float(round(self.risk_amount, 2)),
            "Nocional $": float(round(self.notional, 2)),
            "Estrategia": self.strategy,
        }


def size_position(
    signal: Signal,
    equity: Decimal,
    config: RiskConfig | None = None,
) -> tuple[Decimal, Decimal, str]:
    """Calcula cuántas unidades comprar o vender según una señal.

    Args:
        signal: Señal de la estrategia, con precio de referencia y nivel de
            invalidación.
        equity: Capital actual de la cartera.
        config: Parámetros de riesgo.

    Returns:
        Tupla `(unidades, riesgo_en_divisa, explicación)`. Devuelve cero
        unidades si la señal no permite dimensionar con seguridad.

    Note:
        Si la señal no trae nivel de invalidación, NO se abre posición. Es
        deliberado: una posición sin punto de salida definido no tiene riesgo
        acotado, y por tanto no se puede dimensionar. Preferimos no operar a
        operar con riesgo desconocido.
    """
    cfg = config or RiskConfig()

    if signal.direction is Direction.FLAT:
        return Decimal(0), Decimal(0), "Señal sin dirección"

    if signal.stop_price is None:
        return (
            Decimal(0),
            Decimal(0),
            "Sin nivel de invalidación: riesgo no acotado, no se opera",
        )

    precio = signal.entry_price
    distancia = abs(precio - signal.stop_price)
    if distancia <= 0:
        return Decimal(0), Decimal(0), "Distancia al stop nula"

    riesgo_objetivo = equity * (cfg.risk_per_trade_pct / Decimal(100))
    unidades = riesgo_objetivo / distancia

    # Tope por peso nocional: evita que un stop muy cercano genere una
    # posición desproporcionada respecto al tamaño de la cartera.
    nocional_max = equity * cfg.max_position_weight
    unidades_max = nocional_max / precio
    limitada = unidades > unidades_max
    if limitada:
        unidades = unidades_max

    riesgo_real = unidades * distancia
    explicacion = (
        f"Riesgo objetivo {riesgo_objetivo:,.0f} $ "
        f"({cfg.risk_per_trade_pct}% del capital) dividido entre una distancia "
        f"al stop de {distancia:,.2f}"
    )
    if limitada:
        explicacion += (
            f". Limitada por el peso máximo por posición "
            f"({cfg.max_position_weight:.0%} del capital); "
            f"riesgo real {riesgo_real:,.0f} $"
        )
    return unidades, riesgo_real, explicacion


def select_signals(
    signals: list[Signal], config: RiskConfig | None = None
) -> list[Signal]:
    """Selecciona qué señales operar cuando hay más candidatas que huecos.

    Criterio: convicción de la estrategia y, a igualdad, orden alfabético para
    que la selección sea determinista y reproducible. Un criterio aleatorio o
    dependiente del orden de llegada haría que dos ejecuciones del sistema
    produjeran carteras distintas con los mismos datos.

    Args:
        signals: Señales candidatas.
        config: Parámetros de riesgo, para el máximo de posiciones.

    Returns:
        Señales seleccionadas, como máximo `max_positions`.
    """
    cfg = config or RiskConfig()
    orden = {"strong": 0, "moderate": 1, "weak": 2}
    accionables = [s for s in signals if s.direction is not Direction.FLAT]
    accionables.sort(key=lambda s: (orden.get(s.strength.value, 9), s.symbol))
    return accionables[: cfg.max_positions]
