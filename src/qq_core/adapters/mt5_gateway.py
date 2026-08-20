"""Adaptador MT5 vía gateway.

FRONTERA ARQUITECTÓNICA CRÍTICA
--------------------------------
Este módulo NO importa `MetaTrader5`. Nunca debe hacerlo.

El paquete `MetaTrader5` es una dependencia nativa de Windows acoplada a una
instalación concreta del terminal. Importarlo desde el núcleo ataría toda la
plataforma —incluidos los tests y el CI— a un host Windows con MT5 abierto y
con sesión iniciada en GBI.

En su lugar: un proceso `mt5-gateway` corre junto al terminal, expone un HTTP
local mínimo y es el ÚNICO componente que toca la librería nativa. El núcleo
habla con él por HTTP. Coste: ~300 líneas y un salto de red local. Beneficio:
el resto de la plataforma es portable, testeable y desplegable en cualquier
sitio.

El servidor del gateway vive en `services/mt5-gateway/` (proceso separado, se
entrega en el paquete del Módulo 01). Aquí solo está el cliente.

SEGURIDAD: sólo lectura
-----------------------
El gateway no expone ningún endpoint de envío de órdenes ni importa las
funciones de ejecución de la librería nativa. Es una garantía estructural, no
una convención: no existe código capaz de operar, así que ningún bug ni ningún
error humano puede provocarlo.

El test CA-11 escanea el árbol de `qq_core` en busca de los identificadores de
ejecución de MT5 y falla el CI si aparecen. La comprobación es puramente
textual: ni siquiera un docstring que los mencione pasa. Es agresivo a
propósito.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from pydantic import ValidationError

from qq_core.domain.bar import Bar
from qq_core.domain.enums import AssetClass, DataSource, Timeframe
from qq_core.domain.provenance import Provenance, hash_payload
from qq_core.ports.data_provider import (
    FetchResult,
    ProviderCapabilities,
    ProviderContractError,
    ProviderError,
)

MT5_CAPABILITIES = ProviderCapabilities(
    source=DataSource.MT5_GBI,
    timeframes=frozenset(
        {
            Timeframe.M1,
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        }
    ),
    asset_classes=frozenset({AssetClass.CFD, AssetClass.FX_SPOT, AssetClass.FUTURE}),
    max_bars_per_request=50_000,
    rate_limit_per_minute=None,
    has_point_in_time=False,
    adjusts_corporate_actions=False,
    is_broker_specific=True,
    supports_volume=False,
    notes=(
        "PRECIOS DEL FEED DE GBI, no del mercado subyacente.",
        "`volume` de MT5 es tick volume, NO volumen real: se descarta.",
        "El histórico depende de lo que el terminal haya descargado; puede "
        "cambiar entre sesiones. Vigilar bar_revision.",
        "VERIFICAR si los símbolos son futuros reales o CFDs antes de "
        "modelar carry o term structure.",
    ),
)

_MT5_TIMEFRAME_CODE: dict[Timeframe, str] = {
    Timeframe.M1: "M1",
    Timeframe.M5: "M5",
    Timeframe.M15: "M15",
    Timeframe.H1: "H1",
    Timeframe.H4: "H4",
    Timeframe.D1: "D1",
}


class MT5GatewayProvider:
    """Cliente del gateway MT5. Solo lectura, sin dependencias de Windows.

    Args:
        transport: Función `(url, params) -> str` que devuelve el cuerpo JSON
            de la respuesta del gateway. Inyectada para permitir tests sin
            gateway y sin MT5.
        base_url: URL del gateway. Por defecto localhost, porque el gateway
            NUNCA debe exponerse fuera de la máquina.
    """

    def __init__(
        self,
        transport: Callable[[str, dict[str, Any]], str],
        base_url: str = "http://127.0.0.1:8765",
    ) -> None:
        self._transport = transport
        self._base_url = base_url.rstrip("/")

    @property
    def capabilities(self) -> ProviderCapabilities:
        return MT5_CAPABILITIES

    def fetch_bars(
        self,
        provider_symbol: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> FetchResult:
        """Pide barras al gateway y las convierte al modelo canónico.

        Args:
            provider_symbol: Símbolo tal como lo nombra GBI. Ej. `EURUSD.gbi`.
            canonical_symbol: Símbolo canónico interno.
            timeframe: Resolución solicitada.
            start: Inicio UTC inclusivo.
            end: Fin UTC exclusivo.

        Returns:
            Barras ordenadas con su procedencia.

        Raises:
            ProviderError: Timeframe no soportado o gateway inalcanzable.
            ProviderContractError: Respuesta del gateway malformada.
        """
        if timeframe not in self.capabilities.timeframes:
            raise ProviderError(f"MT5 no soporta el timeframe {timeframe.value}")

        params = {
            "symbol": provider_symbol,
            "timeframe": _MT5_TIMEFRAME_CODE[timeframe],
            "start": start.isoformat(),
            "end": end.isoformat(),
        }
        try:
            payload = self._transport(f"{self._base_url}/bars", params)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(
                f"Gateway MT5 inalcanzable en {self._base_url}: {exc}"
            ) from exc

        bars = self._parse(payload, canonical_symbol, timeframe, start, end)
        provenance = Provenance(
            source=DataSource.MT5_GBI,
            provider_symbol=provider_symbol,
            ingested_at=datetime.now(timezone.utc),
            raw_payload_hash=hash_payload(payload),
            request_params=params,
        )
        return FetchResult(bars=tuple(bars), provenance=provenance)

    def _parse(
        self,
        payload: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Parsea la respuesta JSON del gateway.

        El gateway entrega `time` como epoch en segundos UTC. Se descarta
        `tick_volume` deliberadamente: no es volumen negociado y tratarlo como
        tal produciría features de volumen sin sentido económico.
        """
        try:
            rows = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProviderContractError(f"JSON inválido del gateway: {exc}") from exc

        if not isinstance(rows, list):
            raise ProviderContractError(
                f"Se esperaba una lista del gateway, se recibió {type(rows).__name__}"
            )

        bars: list[Bar] = []
        for row in rows:
            try:
                ts = datetime.fromtimestamp(int(row["time"]), tz=timezone.utc)
                bar = Bar(
                    symbol=canonical_symbol,
                    timeframe=timeframe,
                    ts=ts,
                    source=DataSource.MT5_GBI,
                    open=Decimal(str(row["open"])),
                    high=Decimal(str(row["high"])),
                    low=Decimal(str(row["low"])),
                    close=Decimal(str(row["close"])),
                    volume=None,  # tick_volume != volumen real; ver notes
                    open_interest=None,
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                raise ProviderContractError(
                    f"Fila inválida del gateway MT5: {row} ({exc})"
                ) from exc
            if start <= bar.ts < end:
                bars.append(bar)

        bars.sort(key=lambda b: b.ts)
        return bars
