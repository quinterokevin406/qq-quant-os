"""Puerto de proveedor de datos (arquitectura hexagonal).

El núcleo depende de esta interfaz, nunca de un proveedor concreto. Es la
implementación literal del requisito "cambiar proveedores sin modificar el
resto del sistema".

Un adaptador que no pueda cumplir este contrato NO se adapta forzándolo: se
documenta la limitación en `ProviderCapabilities` y el orquestador decide.
Ocultar una limitación del proveedor detrás de una interfaz uniforme es cómo
se cuelan datos incorrectos sin que nadie se entere.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from qq_core.domain.bar import Bar
from qq_core.domain.enums import AssetClass, DataSource, Timeframe
from qq_core.domain.provenance import Provenance


@dataclass(frozen=True)
class ProviderCapabilities:
    """Lo que un proveedor puede y no puede hacer, declarado explícitamente.

    Attributes:
        source: Identidad del proveedor.
        timeframes: Resoluciones soportadas.
        asset_classes: Clases de activo que sirve.
        max_bars_per_request: Tope de barras por llamada. Determina el tamaño
            de los chunks de ingesta.
        rate_limit_per_minute: Peticiones por minuto permitidas. `None` = sin
            límite conocido. Alpha Vantage en plan gratuito es 5/min, lo que lo
            descalifica para cualquier cosa que no sea ingesta diaria batch.
        has_point_in_time: Si el proveedor entrega el valor tal como se conocía
            en una fecha pasada. Casi ninguno lo hace. Si es `False`, cualquier
            estrategia construida sobre estos datos tiene riesgo de lookahead
            que debe mitigarse en el Módulo 02.
        adjusts_corporate_actions: Si ajusta splits y dividendos.
        is_broker_specific: Si los precios son del feed de un bróker concreto y
            no del mercado subyacente. `True` para MT5/GBI.
        supports_volume: Si publica volumen real (no tick volume).
    """

    source: DataSource
    timeframes: frozenset[Timeframe]
    asset_classes: frozenset[AssetClass]
    max_bars_per_request: int
    rate_limit_per_minute: int | None = None
    has_point_in_time: bool = False
    adjusts_corporate_actions: bool = False
    is_broker_specific: bool = False
    supports_volume: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FetchResult:
    """Resultado de una petición de barras, con su procedencia adjunta.

    Las barras nunca viajan sin procedencia. El tipo lo hace imposible.
    """

    bars: tuple[Bar, ...]
    provenance: Provenance


class ProviderError(RuntimeError):
    """Error recuperable del proveedor (red, rate limit, respuesta vacía)."""


class ProviderContractError(ProviderError):
    """El proveedor devolvió datos que violan el contrato declarado.

    Se distingue de `ProviderError` porque NO debe reintentarse: reintentar una
    respuesta malformada solo consume cuota. Requiere intervención humana.
    """


@runtime_checkable
class DataProvider(Protocol):
    """Contrato que todo adaptador de datos debe cumplir."""

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Capacidades y limitaciones declaradas de este proveedor."""
        ...

    def fetch_bars(
        self,
        provider_symbol: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> FetchResult:
        """Obtiene barras en el rango [start, end).

        Args:
            provider_symbol: Símbolo en el dialecto del proveedor.
            canonical_symbol: Símbolo canónico a estampar en las barras.
            timeframe: Resolución solicitada.
            start: Inicio del rango, UTC aware, inclusivo.
            end: Fin del rango, UTC aware, EXCLUSIVO. La semiapertura evita
                el solapamiento de un tick entre chunks consecutivos.

        Returns:
            Barras ordenadas ascendentemente por `ts`, más su procedencia.

        Raises:
            ProviderError: Fallo transitorio; el orquestador reintentará.
            ProviderContractError: Respuesta inválida; no reintentar.
        """
        ...
