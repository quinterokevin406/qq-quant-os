"""Modelo de instrumento: unión discriminada por clase de activo.

DECISIÓN CENTRAL (ADR-0001)
---------------------------
No existe un `Instrument` genérico con campos opcionales para todas las clases
de activo. En su lugar hay una clase base mínima con lo que es *universalmente
cierto*, y una subclase por clase de activo con lo que es *obligatoriamente
cierto* para ella.

Un `expiry: date | None` en la clase base convierte un error de modelado en un
`None` silencioso que se propaga hasta producción. Con la unión discriminada,
construir un `Future` sin `expiry` falla en el momento de la validación.

El precio del enfoque es que el código consumidor debe hacer `match` sobre
`asset_class`. Eso es deliberado: obliga a decidir explícitamente qué hace una
estrategia con un bono cuando fue escrita para futuros.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qq_core.domain.enums import AssetClass, PriceQuoteType, RollPolicy


class InstrumentBase(BaseModel):
    """Atributos verdaderos para *cualquier* cosa que tenga una serie de precios.

    Attributes:
        symbol: Identificador canónico interno, independiente del proveedor.
            Convención: MAYÚSCULAS, sin espacios. Ej. `ES`, `EURUSD`, `AAPL`.
            NO usar el símbolo del bróker aquí; el mapeo vive en `SymbolMap`.
        name: Nombre legible por humanos. Solo para presentación.
        currency: Divisa de cotización en ISO-4217. `None` únicamente cuando
            `quote_type` es `INDEX_LEVEL`, donde no hay divisa asociada.
        tick_size: Incremento mínimo de precio. `Decimal`, nunca `float`:
            un tick de 0.01 no es representable en binario y los errores de
            redondeo se acumulan en el cálculo de PnL.
        quote_type: Qué representa numéricamente el precio.
        active: Si el instrumento sigue siendo negociable/ingestable.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    symbol: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=128)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    tick_size: Decimal = Field(gt=0)
    quote_type: PriceQuoteType = PriceQuoteType.PRICE
    active: bool = True

    @field_validator("symbol")
    @classmethod
    def _symbol_is_canonical(cls, v: str) -> str:
        if v != v.strip().upper():
            raise ValueError("symbol debe estar en mayúsculas y sin espacios externos")
        return v


class Future(InstrumentBase):
    """Contrato de futuro individual (no continuo).

    Un `Future` es SIEMPRE un contrato concreto con vencimiento. La serie
    continua es un artefacto derivado que produce el Módulo 02, no un
    instrumento primario.

    Attributes:
        root: Raíz del contrato. Ej. `ES` para ESZ25.
        expiry: Fecha de último día de negociación.
        contract_multiplier: Valor monetario de 1.0 de movimiento de precio.
        exchange: Bolsa donde cotiza (MIC o nombre corto). Distingue un futuro
            real de un CFD sobre futuro.
        roll_policy: Política declarada para construir el continuo. Declarativa
            en este módulo; se aplica en el Módulo 02.
    """

    asset_class: Literal[AssetClass.FUTURE] = AssetClass.FUTURE
    root: str = Field(min_length=1, max_length=16)
    expiry: date
    contract_multiplier: Decimal = Field(gt=0)
    exchange: str = Field(min_length=1, max_length=32)
    roll_policy: RollPolicy = RollPolicy.PANAMA


class FinancingTerms(BaseModel):
    """Coste de mantener una posición abierta de un día para otro.

    REPRESENTACIÓN: TASA ANUAL, NO PUNTOS
    --------------------------------------
    MetaTrader expresa el swap en "puntos" del instrumento. Esa unidad depende
    de la escala de precio de cada activo, lo que la hace intransferible: 183
    puntos sobre un índice a 5.000 son un 0,037% diario, y sobre el oro a 2.000
    son un 0,09%. Copiar el valor en puntos de un instrumento a otro produce
    costes erróneos en un factor de varias veces.

    Por eso el modelo almacena la **tasa anual sobre el nocional**, que es
    invariante de escala y comparable entre activos. La conversión desde
    puntos de MT5 se hace una sola vez, en `from_mt5_points`, con el precio de
    referencia del momento de la medición.

    Attributes:
        annual_rate_long: Tasa anual en posición comprada, en tanto por uno.
            Negativa = coste. Ej. -0.134 = 13,4% anual de coste.
        annual_rate_short: Tasa anual en posición vendida. Positiva = ingreso.
        triple_swap_weekday: Día en que se cobra triple, cubriendo el fin de
            semana. 0=lunes ... 4=viernes.
        measured: `True` si procede de la especificación real del bróker;
            `False` si es una estimación. Distinguirlo es esencial: un backtest
            basado en costes estimados no tiene la misma validez que uno basado
            en costes medidos, y quien lea el resultado debe saberlo.
        note: Origen del dato, para auditoría.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    annual_rate_long: Decimal
    annual_rate_short: Decimal
    triple_swap_weekday: int = Field(ge=0, le=6, default=4)
    measured: bool = False
    note: str = Field(default="", max_length=200)

    @classmethod
    def from_mt5_points(
        cls,
        swap_points_long: Decimal | str,
        swap_points_short: Decimal | str,
        point_size: Decimal | str,
        reference_price: Decimal | str,
        triple_swap_weekday: int = 4,
        note: str = "",
    ) -> "FinancingTerms":
        """Convierte swaps de MetaTrader (en puntos) a tasa anual.

        Args:
            swap_points_long: Valor de `Swap long` de la especificación.
            swap_points_short: Valor de `Swap short`.
            point_size: Tamaño del punto, 10 elevado a -digits.
            reference_price: Precio del instrumento al hacer la medición.
            triple_swap_weekday: Día del cargo triple.
            note: Origen del dato.

        Returns:
            Términos con tasas anuales equivalentes.

        Note:
            El cargo se aplica los 7 días de la semana (5 sesiones, con una
            triple), de modo que el factor de anualización es 365, no 252.
        """
        p_long = Decimal(str(swap_points_long))
        p_short = Decimal(str(swap_points_short))
        punto = Decimal(str(point_size))
        precio = Decimal(str(reference_price))
        if precio <= 0:
            raise ValueError("reference_price debe ser positivo")

        diaria_long = (p_long * punto) / precio
        diaria_short = (p_short * punto) / precio
        return cls(
            annual_rate_long=(diaria_long * 365).quantize(Decimal("0.000001")),
            annual_rate_short=(diaria_short * 365).quantize(Decimal("0.000001")),
            triple_swap_weekday=triple_swap_weekday,
            measured=True,
            note=note,
        )

    @classmethod
    def estimated(
        cls,
        annual_rate_long: str = "-0.09",
        annual_rate_short: str = "0.02",
        note: str = "Estimación; pendiente de medir en el terminal",
    ) -> "FinancingTerms":
        """Estimación conservadora para instrumentos aún no medidos.

        Los valores por defecto reflejan una financiación típica de CFD:
        aproximadamente el tipo de referencia más un margen del bróker en el
        lado comprado, y una fracción de ese tipo abonada en el lado vendido.

        Queda marcada como NO medida para que ningún informe la presente con
        la misma confianza que un dato real.
        """
        return cls(
            annual_rate_long=Decimal(annual_rate_long),
            annual_rate_short=Decimal(annual_rate_short),
            measured=False,
            note=note,
        )

    def daily_rate(self, is_long: bool, weekday: int) -> Decimal:
        """Coste diario como fracción del nocional.

        Args:
            is_long: `True` para posición comprada.
            weekday: Día de la semana (0=lunes ... 6=domingo).

        Returns:
            Fracción del nocional. Negativa = coste, positiva = ingreso.
        """
        anual = self.annual_rate_long if is_long else self.annual_rate_short
        diaria = anual / Decimal(365)
        if weekday == self.triple_swap_weekday:
            diaria *= 3
        return diaria

    def weekly_rate(self, is_long: bool) -> Decimal:
        """Coste de una semana completa de posición, como fracción del nocional."""
        return sum(
            (self.daily_rate(is_long, d) for d in range(5)), start=Decimal(0)
        )

    def annual_pct(self, is_long: bool) -> float:
        """Tasa anual en porcentaje, para informes."""
        rate = self.annual_rate_long if is_long else self.annual_rate_short
        return float(rate) * 100


class CFD(InstrumentBase):
    """Contrato por diferencia emitido por un bróker.

    Existe como clase separada de `Future` por una razón económica, no técnica:
    un CFD sobre un índice NO es el futuro del índice. Su financiamiento, su
    vencimiento y su spread los fija el bróker. Backtestear carry o term
    structure sobre CFDs mide la política de precios del bróker, no la curva
    del mercado.

    Attributes:
        issuer: Bróker emisor. Un CFD de GBE y uno de otro bróker no son el
            mismo instrumento aunque sigan al mismo subyacente.
        underlying_ref: Referencia textual al subyacente, informativa.
        contract_multiplier: Valor monetario de 1.0 de movimiento.
        financing: Términos de financiación medidos del bróker. `None` sólo
            si aún no se han auditado.
    """

    asset_class: Literal[AssetClass.CFD] = AssetClass.CFD
    issuer: str = Field(min_length=1, max_length=32)
    underlying_ref: str = Field(min_length=1, max_length=64)
    contract_multiplier: Decimal = Field(gt=0)
    financing: FinancingTerms | None = None

    @property
    def financing_applies(self) -> bool:
        """Si el instrumento acumula coste de financiación overnight."""
        return self.financing is not None


class FxSpot(InstrumentBase):
    """Par de divisas al contado.

    Attributes:
        base: Divisa base ISO-4217 (la que se compra).
        quote: Divisa cotizada ISO-4217. Debe coincidir con `currency`.
        pip_size: Tamaño del pip. Distinto de `tick_size`: EURUSD tiene pip
            0.0001 y tick 0.00001 en feeds de 5 dígitos.
        financing: Coste de mantener la posición de un día para otro.

            AÑADIDO EN v1.10. Hasta entonces `FxSpot` no tenía este campo, y
            `financing_from_instrument` devolvía `None` para todo lo que no
            fuera CFD. Consecuencia: **los backtests de divisas se ejecutaban
            sin ningún coste de financiación**, lo que inflaba el resultado de
            los pares donde mantener la posición cuesta dinero.

            La medición del 2026-08-19 mostró que el coste real de los pares va
            de -2,7% a +2,7% anual según el par y la dirección: ni despreciable
            ni uniforme. Tratarlo como cero era un error material.
    """

    asset_class: Literal[AssetClass.FX_SPOT] = AssetClass.FX_SPOT
    base: str = Field(pattern=r"^[A-Z]{3}$")
    quote: str = Field(pattern=r"^[A-Z]{3}$")
    pip_size: Decimal = Field(gt=0)
    financing: FinancingTerms | None = None


class Equity(InstrumentBase):
    """Acción individual.

    Attributes:
        exchange: Bolsa primaria de cotización.
        isin: Identificador ISIN. Es el único identificador estable frente a
            cambios de ticker; sin él, un cambio de ticker rompe el histórico.
        adjusted_for_corporate_actions: Si la serie asociada ya viene ajustada
            por splits y dividendos. Se declara aquí porque mezclar series
            ajustadas y no ajustadas es una de las fuentes de error más
            silenciosas en un backtest de renta variable.
    """

    asset_class: Literal[AssetClass.EQUITY] = AssetClass.EQUITY
    exchange: str = Field(min_length=1, max_length=32)
    isin: str | None = Field(default=None, pattern=r"^[A-Z]{2}[A-Z0-9]{9}\d$")
    adjusted_for_corporate_actions: bool = False


class ETF(Equity):
    """ETF. Comparte la mecánica de `Equity` y añade el subyacente replicado."""

    asset_class: Literal[AssetClass.ETF] = AssetClass.ETF  # type: ignore[assignment]
    tracks: str | None = Field(default=None, max_length=64)


class MacroSeries(InstrumentBase):
    """Serie macroeconómica (FRED, etc.). No es negociable.

    Se modela como instrumento porque comparte el pipeline de ingesta, pero se
    marca explícitamente para que ninguna estrategia pueda tomar posición sobre
    ella. La distinción crítica es `revision_lag_days`: los datos macro se
    revisan después de su publicación, y usar el valor final en una fecha
    anterior a su publicación es lookahead bias puro.

    Attributes:
        series_id: Identificador en el proveedor. Ej. `DGS10`.
        unit: Unidad de medida legible. Ej. `Percent`, `Index 2012=100`.
        revision_lag_days: Días típicos entre el periodo de referencia y la
            primera publicación. El Módulo 02 lo usa para el desplazamiento
            point-in-time.
    """

    asset_class: Literal[AssetClass.MACRO_SERIES] = AssetClass.MACRO_SERIES
    series_id: str = Field(min_length=1, max_length=32)
    unit: str = Field(min_length=1, max_length=64)
    revision_lag_days: int = Field(ge=0, default=0)
    tick_size: Decimal = Decimal("0.0001")
    quote_type: PriceQuoteType = PriceQuoteType.INDEX_LEVEL


Instrument = Annotated[
    Union[Future, CFD, FxSpot, Equity, ETF, MacroSeries],
    Field(discriminator="asset_class"),
]
"""Unión discriminada de instrumentos.

Uso:
    >>> from pydantic import TypeAdapter
    >>> adapter = TypeAdapter(Instrument)
    >>> inst = adapter.validate_python({"asset_class": "fx_spot", ...})
"""
