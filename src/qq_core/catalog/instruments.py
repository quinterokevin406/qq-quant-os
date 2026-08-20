"""Catálogo de instrumentos de QQ Quant OS.

Contiene el universo de inversión verificado instrumento por instrumento sobre
el terminal MT5 de GBE Global (auditoría del 2026-08-06, ver ADR-0003).

PRINCIPIO DE SEPARACIÓN INVESTIGACIÓN / EJECUCIÓN
--------------------------------------------------
Cada instrumento tiene dos identidades:

  - `mt5_symbol`: dónde se EJECUTA la operación (feed de GBE).
  - `stooq_symbol`: de dónde salen los DATOS de investigación (fuente
    independiente, histórico largo).

Están separados deliberadamente. El feed de un bróker es específico de ese
bróker: tiene su spread, su horario y su histórico limitado. Investigar sobre
él significa optimizar contra las particularidades del proveedor en lugar de
contra el mercado. Usar una fuente independiente para investigar y el bróker
para ejecutar es la práctica estándar en la industria.

El coste de esta separación es el tracking error entre ambas series, que el
Módulo 02 debe medir y reportar. Es un coste conocido y acotado, preferible al
riesgo de sobreajustar a un feed propietario.

ADVERTENCIA SOBRE LOS SÍMBOLOS DE STOOQ
----------------------------------------
Los identificadores de Stooq no están documentados oficialmente. Los de este
catálogo son los de uso habitual, pero DEBEN verificarse empíricamente: el
script `scripts/verificar_catalogo.py` intenta descargar cada uno y reporta
cuáles responden. Un símbolo que no responde no es un fallo del sistema, es
información que hay que registrar y corregir.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from qq_core.domain.enums import AssetClass, DataSource
from qq_core.domain.instrument import CFD, FinancingTerms, FxSpot, Instrument

BROKER = "GBE Global"
"""Emisor de todos los CFDs del catálogo. Ver ADR-0003."""

# LIMITACIÓN CONOCIDA — ORO Y PLATA
# ---------------------------------
# Yahoo Finance no publica el precio al contado de los metales con un
# identificador estable: `XAUUSD=X` no existe. La alternativa disponible es el
# futuro del COMEX (`GC=F` para el oro, `SI=F` para la plata).
#
# Consecuencia: los precios y las medias móviles del sistema NO coinciden
# exactamente con los del terminal del bróker, que cotiza contado. El futuro
# incorpora el coste de financiación hasta el vencimiento, así que cotiza con
# una prima sobre el contado que varía con los tipos de interés.
#
# La diferencia es de nivel, no de forma: la tendencia y los cruces de medias
# se comportan igual porque ambas series siguen al mismo activo. Aun así, los
# valores absolutos difieren y eso debe advertirse al operador.
#
# Solución definitiva: conectar el terminal del bróker (gateway MT5) y usar su
# propio feed también para investigación en estos instrumentos.
METALES_USAN_FUTUROS = ("XAUUSD", "XAGUSD")


@dataclass(frozen=True)
class CatalogEntry:
    """Un instrumento del universo con su mapeo a cada proveedor.

    Attributes:
        instrument: Definición canónica del instrumento.
        mt5_symbol: Símbolo exacto en el terminal de GBE. `None` si el
            instrumento no es operable en el bróker.
        stooq_symbol: Símbolo en Stooq. Conservado como fuente alternativa,
            aunque Stooq bloquea actualmente las descargas automáticas.
        yahoo_symbol: Símbolo en Yahoo Finance. Fuente principal de datos
            diarios de investigación.
        twelvedata_symbol: Símbolo en Twelve Data. Es la única fuente del
            catálogo con datos intradía (5 minutos y 1 hora).
        group: Agrupación funcional para el panel y los informes.
        core: `True` si forma parte del universo principal; `False` si se
            incluye sólo para ampliar la muestra de momentum transversal.
    """

    instrument: Instrument
    mt5_symbol: str | None
    stooq_symbol: str | None
    group: str
    core: bool = True
    yahoo_symbol: str | None = None
    twelvedata_symbol: str | None = None

    @property
    def symbol(self) -> str:
        """Símbolo canónico interno."""
        return self.instrument.symbol

    def provider_symbol(self, source: DataSource) -> str | None:
        """Símbolo de este instrumento en el proveedor indicado."""
        if source is DataSource.TWELVEDATA:
            return self.twelvedata_symbol
        if source is DataSource.YAHOO:
            return self.yahoo_symbol
        if source is DataSource.STOOQ:
            return self.stooq_symbol
        if source is DataSource.MT5_GBI:
            return self.mt5_symbol
        return None


def _index_cfd(
    symbol: str,
    name: str,
    currency: str,
    *,
    financing: FinancingTerms | None = None,
    tick_size: str = "0.01",
    multiplier: str = "1",
) -> CFD:
    """Construye un CFD con los parámetros del catálogo.

    Si no se pasan términos de financiación medidos, se asigna la estimación
    conservadora, marcada como no medida. Nunca se copian los puntos de swap
    de un instrumento a otro: la unidad depende de la escala de precio y el
    resultado sería erróneo.
    """
    return CFD(
        symbol=symbol,
        name=name,
        currency=currency,
        tick_size=Decimal(tick_size),
        issuer=BROKER,
        underlying_ref=name,
        contract_multiplier=Decimal(multiplier),
        financing=financing or FinancingTerms.estimated(),
    )


# --------------------------------------------------------------------------- #
# COSTES DE FINANCIACIÓN MEDIDOS — terminal GBE Global, 2026-08-19
# --------------------------------------------------------------------------- #
# Medidos con `scripts/medir_swaps.py` sobre la cuenta 800878 en
# GBEGlobal-Server. Sustituyen la estimación del -9% anual que usaban 22 de los
# 23 instrumentos.
#
# LO QUE REVELÓ LA MEDICIÓN, Y POR QUÉ IMPORTA
# ---------------------------------------------
# La estimación del -9% era razonable para índices y metales, y GRAVEMENTE
# equivocada para divisas y petróleo:
#
#   Divisas    coste real entre -2,7% y +2,7%   (error de 6 a 12 puntos)
#   Petróleo   comprado COBRA +4,9% y +19,1%    (error de 14 a 28 puntos)
#   Metales    -5,2% y -8,4%                    (estimación aceptable)
#   Índices US -7,9% a -10,0%                   (estimación aceptable)
#   Índices EU -15,2%                           (infraestimada 6 puntos)
#
# Consecuencia: durante todos los backtests anteriores, las divisas y el
# petróleo pagaron un coste inventado de hasta 28 puntos porcentuales anuales.
# En el primer informe de validación no apareció NI UN SOLO par de divisas
# entre las diez mejores combinaciones. Ahora sabemos por qué.
#
# ADVERTENCIA SOBRE LA CADUCIDAD DE ESTOS DATOS
# ----------------------------------------------
# US500 estaba documentado en ADR-0003 a -13,36% anual (medición de 2026-08-06)
# y hoy mide -8,63%. Los swaps siguen a los tipos de interés y cambian. Estas
# cifras NO son permanentes: hay que volver a ejecutar `medir_swaps.py`
# periódicamente. Un coste medido hace un año es tan poco fiable como una
# estimación.

_MEDIDO = "Medido en terminal GBE Global, 2026-08-19, scripts/medir_swaps.py"


def _fin(largo: str, corto: str, punto: str, precio: str, triple: int = 4):
    """Atajo para declarar financiación medida desde puntos de MT5."""
    return FinancingTerms.from_mt5_points(
        swap_points_long=largo,
        swap_points_short=corto,
        point_size=punto,
        reference_price=precio,
        triple_swap_weekday=triple,
        note=_MEDIDO,
    )


# Divisas. Triple el miércoles (rollover_3days=3 en MT5 -> índice 2).
EURUSD_FINANCING = _fin("-8.541", "1.491", "0.00001", "1.16631", 2)
GBPUSD_FINANCING = _fin("-2.820", "-2.995", "0.00001", "1.36144", 2)
AUDUSD_FINANCING = _fin("-1.521", "-3.299", "0.00001", "0.71166", 2)
NZDUSD_FINANCING = _fin("-3.884", "0.681", "0.00001", "0.59233", 2)
USDCAD_FINANCING = _fin("3.013", "-9.044", "0.00001", "1.38163", 3)
USDCHF_FINANCING = _fin("5.901", "-12.355", "0.00001", "0.80167", 2)
USDJPY_FINANCING = _fin("7.119", "-18.629", "0.001", "158.342", 2)

# Metales.
XAUUSD_FINANCING = _fin("-64.08", "29.192", "0.01", "4474.01", 2)
XAGUSD_FINANCING = _fin("-15.005", "3.036", "0.001", "65.174", 2)

# Índices. Triple el viernes.
USTEC_FINANCING = _fin("-697.917", "234.549", "0.01", "29309.71", 4)
DE40_FINANCING = _fin("-714.863", "1.631", "0.01", "26082.43", 4)
DJ30_FINANCING = _fin("-1163.577", "481.72", "0.01", "53614.0", 4)
STOXX50_FINANCING = _fin("-269.513", "0.119", "0.01", "6443.9", 4)
NE25_FINANCING = _fin("-45.956", "0.019", "0.01", "1101.74", 4)
ES35_FINANCING = _fin("-829.805", "0.365", "0.01", "19842.12", 4)

# Energía. Comprado COBRA: es carry de backwardation, no un error.
USOIL_FINANCING = _fin("11.359", "-39.292", "0.001", "85.423", 4)
UKOIL_FINANCING = _fin("48.163", "-109.078", "0.001", "92.111", 4)


US500_FINANCING = _fin("-182.313", "61.27", "0.01", "7713.13", 4)
"""Remedido 2026-08-19: -8,63% anual comprado (antes -13,36% en ADR-0003).

La diferencia no es un error de medición: los swaps siguen a los tipos y
cambian con el tiempo. Es la prueba de que estas cifras caducan.
"""
"""Único instrumento con financiación MEDIDA. El resto usa estimación.

Medir los 23 en el terminal es una tarea pendiente de la checklist del
Módulo 01. Hasta entonces, los backtests de los demás instrumentos llevan
costes estimados, y así debe declararse en cualquier informe.
"""


def _fx(
    symbol: str,
    name: str,
    base: str,
    quote: str,
    jpy: bool = False,
    financing: FinancingTerms | None = None,
) -> FxSpot:
    """Construye un par de divisas al contado.

    Los pares con JPY cotizan con 3 decimales en lugar de 5; el tamaño del pip
    cambia en consecuencia. Modelarlo mal produce errores de dos órdenes de
    magnitud en el cálculo de posición.
    """
    return FxSpot(
        symbol=symbol,
        name=name,
        currency=quote,
        tick_size=Decimal("0.001") if jpy else Decimal("0.00001"),
        base=base,
        quote=quote,
        pip_size=Decimal("0.01") if jpy else Decimal("0.0001"),
        financing=financing,
    )


# --------------------------------------------------------------------------- #
# ÍNDICES PRINCIPALES
# --------------------------------------------------------------------------- #
_INDICES_CORE = [
    CatalogEntry(
        _index_cfd("US500", "S&P 500", "USD", financing=US500_FINANCING),
        mt5_symbol="US500.c", stooq_symbol="^spx", yahoo_symbol="^GSPC", twelvedata_symbol="SPX", group="Índices"),
    CatalogEntry(
        _index_cfd("USTEC", "Nasdaq 100", "USD", financing=USTEC_FINANCING),
        mt5_symbol="USTEC.c", stooq_symbol="^ndq", yahoo_symbol="^NDX", twelvedata_symbol="NDX", group="Índices"),
    CatalogEntry(
        _index_cfd("DJ30", "Dow Jones 30", "USD", financing=DJ30_FINANCING),
        mt5_symbol="DJ30.c", stooq_symbol="^dji", yahoo_symbol="^DJI", twelvedata_symbol="DJI", group="Índices"),
    CatalogEntry(
        _index_cfd("DE40", "DAX 40", "EUR", financing=DE40_FINANCING),
        mt5_symbol="DE40.c", stooq_symbol="^dax", yahoo_symbol="^GDAXI", twelvedata_symbol="DAX", group="Índices"),
    CatalogEntry(
        _index_cfd("US2000", "Russell 2000", "USD"),
        mt5_symbol="US2000.c", stooq_symbol="^rut", yahoo_symbol="^RUT", twelvedata_symbol="RUT", group="Índices"),
]

# --------------------------------------------------------------------------- #
# ÍNDICES ADICIONALES — amplían la muestra de momentum transversal
# --------------------------------------------------------------------------- #
_INDICES_EXTRA = [
    CatalogEntry(
        _index_cfd("UK100", "FTSE 100", "GBP"),
        mt5_symbol="UK100.c", stooq_symbol="^ukx", yahoo_symbol="^FTSE", twelvedata_symbol="FTSE", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("F40", "CAC 40", "EUR"),
        mt5_symbol="F40.c", stooq_symbol="^cac", yahoo_symbol="^FCHI", twelvedata_symbol="CAC", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("ES35", "IBEX 35", "EUR", financing=ES35_FINANCING),
        mt5_symbol="ES35.c", stooq_symbol="^ibex", yahoo_symbol="^IBEX", twelvedata_symbol="IBEX", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("NE25", "AEX 25", "EUR", financing=NE25_FINANCING),
        mt5_symbol="NE25.c", stooq_symbol="^aex", yahoo_symbol="^AEX", twelvedata_symbol="AEX", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("SWI20", "SMI 20", "CHF"),
        mt5_symbol="SWI20.c", stooq_symbol="^smi", yahoo_symbol="^SSMI", twelvedata_symbol="SMI", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("STOXX50", "Euro Stoxx 50", "EUR", financing=STOXX50_FINANCING),
        mt5_symbol="STOXX50.c", stooq_symbol="^stx", yahoo_symbol="^STOXX50E", twelvedata_symbol="STOXX50E", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("HK50", "Hang Seng", "HKD"),
        mt5_symbol="HK50.c", stooq_symbol="^hsi", yahoo_symbol="^HSI", twelvedata_symbol="HSI", group="Índices", core=False),
    CatalogEntry(
        _index_cfd("AUS200", "ASX 200", "AUD"),
        mt5_symbol="AUS200.c", stooq_symbol="^axjo", yahoo_symbol="^AXJO", twelvedata_symbol="AXJO", group="Índices", core=False),
]

# --------------------------------------------------------------------------- #
# METALES Y ENERGÍA
# --------------------------------------------------------------------------- #
_COMMODITIES = [
    CatalogEntry(
        CFD(symbol="XAUUSD", name="Oro", currency="USD",
            tick_size=Decimal("0.01"), issuer=BROKER, underlying_ref="Gold Spot",
            contract_multiplier=Decimal("100"),
            financing=XAUUSD_FINANCING),
        mt5_symbol="XAUUSD", stooq_symbol="xauusd", yahoo_symbol="GC=F", twelvedata_symbol="XAU/USD", group="Metales"),
    CatalogEntry(
        CFD(symbol="XAGUSD", name="Plata", currency="USD",
            tick_size=Decimal("0.001"), issuer=BROKER, underlying_ref="Silver Spot",
            contract_multiplier=Decimal("5000"),
            financing=XAGUSD_FINANCING),
        mt5_symbol="XAGUSD", stooq_symbol="xagusd", yahoo_symbol="SI=F", twelvedata_symbol="XAG/USD", group="Metales"),
    CatalogEntry(
        _index_cfd("USOIL", "Petróleo WTI", "USD", financing=USOIL_FINANCING),
        mt5_symbol="USOIL.c", stooq_symbol="cl.f", yahoo_symbol="CL=F", twelvedata_symbol="WTI/USD", group="Energía"),
    CatalogEntry(
        _index_cfd("UKOIL", "Petróleo Brent", "USD", financing=UKOIL_FINANCING),
        mt5_symbol="UKOIL.c", stooq_symbol="cb.f", yahoo_symbol="BZ=F", twelvedata_symbol="BRENT/USD", group="Energía"),
]

# --------------------------------------------------------------------------- #
# DIVISAS
# --------------------------------------------------------------------------- #
_FOREX = [
    CatalogEntry(_fx("EURUSD", "Euro / Dólar", "EUR", "USD", financing=EURUSD_FINANCING),
                 mt5_symbol="EURUSD", stooq_symbol="eurusd", yahoo_symbol="EURUSD=X", twelvedata_symbol="EUR/USD", group="Divisas"),
    CatalogEntry(_fx("GBPUSD", "Libra / Dólar", "GBP", "USD", financing=GBPUSD_FINANCING),
                 mt5_symbol="GBPUSD", stooq_symbol="gbpusd", yahoo_symbol="GBPUSD=X", twelvedata_symbol="GBP/USD", group="Divisas"),
    CatalogEntry(_fx("USDJPY", "Dólar / Yen", "USD", "JPY", jpy=True, financing=USDJPY_FINANCING),
                 mt5_symbol="USDJPY", stooq_symbol="usdjpy", yahoo_symbol="USDJPY=X", twelvedata_symbol="USD/JPY", group="Divisas"),
    CatalogEntry(_fx("AUDUSD", "Dólar australiano / Dólar", "AUD", "USD", financing=AUDUSD_FINANCING),
                 mt5_symbol="AUDUSD", stooq_symbol="audusd", yahoo_symbol="AUDUSD=X", twelvedata_symbol="AUD/USD", group="Divisas"),
    CatalogEntry(_fx("USDCAD", "Dólar / Dólar canadiense", "USD", "CAD", financing=USDCAD_FINANCING),
                 mt5_symbol="USDCAD", stooq_symbol="usdcad", yahoo_symbol="USDCAD=X", twelvedata_symbol="USD/CAD", group="Divisas"),
    CatalogEntry(_fx("USDCHF", "Dólar / Franco suizo", "USD", "CHF", financing=USDCHF_FINANCING),
                 mt5_symbol="USDCHF", stooq_symbol="usdchf", yahoo_symbol="USDCHF=X", twelvedata_symbol="USD/CHF", group="Divisas"),
]


CATALOG: tuple[CatalogEntry, ...] = tuple(
    _INDICES_CORE + _INDICES_EXTRA + _COMMODITIES + _FOREX
)
"""Universo completo verificado. 23 instrumentos."""


BY_SYMBOL: dict[str, CatalogEntry] = {e.symbol: e for e in CATALOG}


def get(symbol: str) -> CatalogEntry:
    """Devuelve una entrada del catálogo por su símbolo canónico.

    Raises:
        KeyError: Si el símbolo no está en el catálogo. Se falla en lugar de
            devolver `None` para que un símbolo mal escrito no se propague
            silenciosamente hasta el motor de estrategias.
    """
    if symbol not in BY_SYMBOL:
        raise KeyError(
            f"'{symbol}' no está en el catálogo. "
            f"Disponibles: {', '.join(sorted(BY_SYMBOL))}"
        )
    return BY_SYMBOL[symbol]


def core_universe() -> tuple[CatalogEntry, ...]:
    """Universo principal: los instrumentos que se operan activamente."""
    return tuple(e for e in CATALOG if e.core)


def by_group(group: str) -> tuple[CatalogEntry, ...]:
    """Instrumentos de una agrupación funcional."""
    return tuple(e for e in CATALOG if e.group == group)


def groups() -> tuple[str, ...]:
    """Agrupaciones presentes en el catálogo, en orden de aparición."""
    seen: list[str] = []
    for e in CATALOG:
        if e.group not in seen:
            seen.append(e.group)
    return tuple(seen)


def with_stooq_symbol() -> tuple[CatalogEntry, ...]:
    """Instrumentos con equivalente conocido en Stooq."""
    return tuple(e for e in CATALOG if e.stooq_symbol)


def with_yahoo_symbol() -> tuple[CatalogEntry, ...]:
    """Instrumentos ingestables desde Yahoo Finance (fuente principal)."""
    return tuple(e for e in CATALOG if e.yahoo_symbol)


def with_twelvedata_symbol() -> tuple[CatalogEntry, ...]:
    """Instrumentos disponibles en Twelve Data, la fuente con intradía."""
    return tuple(e for e in CATALOG if e.twelvedata_symbol)


def asset_class_summary() -> dict[AssetClass, int]:
    """Conteo de instrumentos por clase de activo."""
    counts: dict[AssetClass, int] = {}
    for e in CATALOG:
        ac = e.instrument.asset_class
        counts[ac] = counts.get(ac, 0) + 1
    return counts
