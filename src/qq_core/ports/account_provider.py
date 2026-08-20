"""Puerto `AccountProvider`: estado de la cuenta en el bróker.

POR QUÉ EXISTE ESTE PUERTO
---------------------------
El motor de portafolio necesita saber cuánto capital hay, cuánto margen se está
usando y qué posiciones están realmente abiertas. Esa información sólo existe
dentro de MetaTrader 5. El núcleo `qq_core` no puede —ni debe— importar la
librería `MetaTrader5`: es específica de Windows y ataría todo el sistema a esa
plataforma. Está decidido en ADR-0002 y es la razón de que el gateway de datos
funcione por HTTP.

Este puerto aplica el mismo patrón al estado de la cuenta.

LAS DOS IMPLEMENTACIONES Y CUÁNDO SE USA CADA UNA
---------------------------------------------------
`ManualAccountProvider`  Lee los valores que el operador introduce a mano en el
                         perfil de cuenta. Funciona hoy. Todo el motor opera de
                         verdad sobre esos números.

`MT5AccountProvider`     Lee del gateway. NO IMPLEMENTADO. La estructura, las
                         firmas, las unidades y las pruebas de contrato están
                         escritas; falta el servicio al otro lado.

REGLA INNEGOCIABLE
------------------
Un proveedor que no puede obtener un dato **lanza una excepción**. Nunca
devuelve cero, ni una estimación, ni el último valor conocido.

El motivo no es purismo. Si `margin_used` devolviera 0.0 cuando en realidad no
se sabe, el panel mostraría "margen utilizado: 0%" y alguien abriría posiciones
creyendo que tiene capacidad libre. Un dato ausente que se presenta como dato
real es peor que un error visible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol, runtime_checkable


class AccountProviderError(RuntimeError):
    """Fallo al obtener el estado de la cuenta."""


class AccountDataUnavailable(AccountProviderError):
    """El dato solicitado no está disponible en este proveedor.

    Se usa cuando el proveedor existe pero no puede servir ese campo concreto:
    por ejemplo, el proveedor manual no conoce el margen real del bróker.

    Contiene siempre qué falta y qué haría falta para tenerlo, porque el
    mensaje acaba mostrándose al operador en el panel.
    """

    def __init__(self, field: str, requirement: str) -> None:
        self.field = field
        self.requirement = requirement
        super().__init__(f"'{field}' no disponible. Requiere: {requirement}")


@dataclass(frozen=True)
class AccountState:
    """Fotografía del estado de la cuenta en un instante.

    Todos los importes van en la divisa de la cuenta. Los campos que un
    proveedor no pueda rellenar valen `None`; NUNCA cero.

    Attributes:
        as_of: Momento de la lectura.
        currency: Divisa de la cuenta.
        balance: Saldo sin contar posiciones abiertas.
        equity: Saldo más el resultado flotante. Es la cifra sobre la que se
            calcula el riesgo, no el balance.
        margin_used: Margen bloqueado por las posiciones abiertas.
        margin_free: Margen disponible para nuevas posiciones.
        margin_level_pct: Equity sobre margen usado, en porcentaje. Por debajo
            de cierto nivel el bróker cierra posiciones.
        floating_pnl: Resultado no realizado de las posiciones abiertas.
        realized_pnl: Resultado realizado del periodo.
        leverage: Apalancamiento máximo que concede el bróker.
        is_live: Falso si los datos vienen de introducción manual.
        source: Identificador del proveedor, para trazabilidad.
    """

    as_of: datetime
    currency: str
    balance: Decimal
    equity: Decimal
    margin_used: Decimal | None = None
    margin_free: Decimal | None = None
    margin_level_pct: Decimal | None = None
    floating_pnl: Decimal | None = None
    realized_pnl: Decimal | None = None
    leverage: int | None = None
    is_live: bool = False
    source: str = "desconocido"

    @property
    def missing_fields(self) -> tuple[str, ...]:
        """Campos que este proveedor no ha podido rellenar.

        El panel los muestra como "no disponible — requiere gateway MT5" en
        lugar de una cifra.
        """
        faltan = []
        for campo in (
            "margin_used",
            "margin_free",
            "margin_level_pct",
            "floating_pnl",
            "leverage",
        ):
            if getattr(self, campo) is None:
                faltan.append(campo)
        return tuple(faltan)

    @property
    def is_complete(self) -> bool:
        """Cierto si están todos los campos necesarios para el motor de riesgo."""
        return not self.missing_fields


@dataclass(frozen=True)
class BrokerPosition:
    """Posición abierta según el bróker.

    Es la fuente de verdad frente al libro interno. La discrepancia entre ambos
    es una alerta: significa que el operador ejecutó algo que no registró, o
    que una posición se cerró por stop sin anotarse.

    Attributes:
        ticket: Identificador de la posición en el bróker.
        symbol: Símbolo de ejecución.
        direction: 'long' o 'short'.
        volume: Volumen en lotes.
        open_price: Precio de apertura.
        current_price: Precio actual.
        stop_loss: Nivel de invalidación, si está puesto en el bróker.
        take_profit: Nivel de objetivo, si está puesto.
        swap_accumulated: Coste de financiación acumulado. Dato valioso: es la
            medición real del swap, no la estimación del catálogo.
        profit: Resultado flotante.
        opened_at: Momento de apertura.
    """

    ticket: str
    symbol: str
    direction: str
    volume: Decimal
    open_price: Decimal
    current_price: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    swap_accumulated: Decimal
    profit: Decimal
    opened_at: datetime


@dataclass(frozen=True)
class SymbolFinancing:
    """Coste de financiación de un símbolo, medido en el terminal.

    Reemplaza la estimación del -9% anual que hoy usan 22 de los 23
    instrumentos del catálogo.

    Attributes:
        symbol: Símbolo de ejecución.
        swap_long: Cargo por mantener comprado, en las unidades de `swap_mode`.
        swap_short: Cargo por mantener vendido.
        swap_mode: Modo de cálculo del bróker. CRÍTICO: MT5 expresa el swap en
            puntos, en divisa del margen, o como tipo de interés anual, según
            el símbolo. Interpretar mal este campo produce errores de un orden
            de magnitud. Nunca convertir sin leerlo.
        swap_rollover_3days: Día de la semana con cargo triple (0 = lunes).
        contract_size: Tamaño del contrato, necesario para convertir a
            porcentaje anual.
        point_value: Valor del punto en divisa de la cuenta.
        measured_at: Momento de la medición.
    """

    symbol: str
    swap_long: Decimal
    swap_short: Decimal
    swap_mode: str
    swap_rollover_3days: int
    contract_size: Decimal
    point_value: Decimal
    measured_at: datetime


@runtime_checkable
class AccountProvider(Protocol):
    """Contrato que debe cumplir cualquier fuente de estado de cuenta.

    IMPLEMENTADORES: las pruebas de `tests/test_contrato_cuenta.py` verifican
    este contrato. Ejecutarlas contra una implementación nueva dice si cumple.
    """

    @property
    def name(self) -> str:
        """Identificador legible del proveedor."""
        ...

    @property
    def is_live(self) -> bool:
        """Cierto si los datos vienen del bróker en tiempo real."""
        ...

    def fetch_account_state(self) -> AccountState:
        """Devuelve el estado actual de la cuenta.

        Raises:
            AccountProviderError: Si no se puede contactar con la fuente.
        """
        ...

    def fetch_positions(self) -> list[BrokerPosition]:
        """Devuelve las posiciones abiertas según el bróker.

        Raises:
            AccountDataUnavailable: Si el proveedor no conoce las posiciones.
        """
        ...

    def fetch_financing(self, symbol: str) -> SymbolFinancing:
        """Devuelve el coste de financiación medido de un símbolo.

        Raises:
            AccountDataUnavailable: Si el proveedor no puede medirlo.
        """
        ...
