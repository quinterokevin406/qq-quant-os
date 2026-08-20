"""Enumeraciones canónicas del dominio.

Estas enums forman parte del contrato público de `qq_core`. Añadir un miembro
es un cambio MINOR; renombrar o eliminar uno es MAJOR y requiere un ADR.
"""

from __future__ import annotations

from enum import StrEnum


class AssetClass(StrEnum):
    """Clase de activo. Actúa como discriminador de la unión `Instrument`."""

    FUTURE = "future"
    FX_SPOT = "fx_spot"
    EQUITY = "equity"
    ETF = "etf"
    INDEX = "index"
    BOND = "bond"
    CRYPTO = "crypto"
    CFD = "cfd"
    MACRO_SERIES = "macro_series"


class Timeframe(StrEnum):
    """Resolución temporal de una barra.

    El valor textual es el identificador canónico usado en la base de datos y
    en las claves naturales. NO usar los alias de pandas ni los de MT5 aquí:
    cada adaptador traduce a su propio dialecto en su frontera.
    """

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    @property
    def seconds(self) -> int:
        return _TIMEFRAME_SECONDS[self]


_TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    Timeframe.M1: 60,
    Timeframe.M5: 300,
    Timeframe.M15: 900,
    Timeframe.H1: 3_600,
    Timeframe.H4: 14_400,
    Timeframe.D1: 86_400,
    Timeframe.W1: 604_800,
}


class DataSource(StrEnum):
    """Proveedor de datos de origen. Forma parte de la clave natural de `Bar`."""

    MT5_GBI = "mt5_gbi"
    STOOQ = "stooq"
    YAHOO = "yahoo"
    TWELVEDATA = "twelvedata"
    ALPHA_VANTAGE = "alpha_vantage"
    FRED = "fred"


class RollPolicy(StrEnum):
    """Método de empalme para construir un contrato continuo de futuros.

    Ver ADR-0002. Ninguna de estas políticas se aplica en el Módulo 01: aquí
    solo se *declara* la política del instrumento. El empalme ocurre en el
    Módulo 02 (Data Quality Engine), que es donde vive la lógica de ajuste.
    """

    NONE = "none"
    """Sin empalme: cada contrato se almacena y se consulta por separado."""

    PANAMA = "panama"
    """Ajuste aditivo hacia atrás. Preserva diferencias absolutas."""

    RATIO = "ratio"
    """Ajuste multiplicativo hacia atrás. Preserva retornos porcentuales."""


class PriceQuoteType(StrEnum):
    """Qué representa numéricamente el precio de la barra."""

    PRICE = "price"
    """Precio directo en la divisa de cotización (la mayoría de los casos)."""

    YIELD = "yield"
    """Rendimiento porcentual (bonos cotizados en yield)."""

    INDEX_LEVEL = "index_level"
    """Nivel de índice, sin divisa asociada."""
