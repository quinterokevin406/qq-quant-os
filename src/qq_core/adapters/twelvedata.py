"""Adaptador de Twelve Data.

POR QUÉ ESTE PROVEEDOR Y NO TRADINGVIEW
----------------------------------------
TradingView no publica una API de datos. Existen librerías no oficiales que
raspan su interfaz web, pero violan sus condiciones de uso y dejan de
funcionar cada vez que cambian el sitio. No es una base aceptable para un
sistema de empresa.

Twelve Data sí ofrece una API oficial con plan gratuito. Y aporta algo que
Yahoo no da de forma fiable: **datos intradía**. Con él se pueden analizar
velas de 5 minutos y de 1 hora, que es lo que hace falta para las estrategias
de horizonte corto.

LIMITACIONES DEL PLAN GRATUITO
------------------------------
Ocho peticiones por minuto y unas 800 al día. Suficiente para actualizar el
universo una vez al día, insuficiente para consultas continuas. El límite se
declara en `capabilities` para que el orquestador lo respete en lugar de
chocar contra él.

Requiere una clave gratuita de twelvedata.com, que se configura en la variable
de entorno `TWELVEDATA_API_KEY`.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

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

API_URL = "https://api.twelvedata.com/time_series"
CLAVE_ENV = "TWELVEDATA_API_KEY"

TWELVEDATA_CAPABILITIES = ProviderCapabilities(
    source=DataSource.TWELVEDATA,
    timeframes=frozenset(
        {
            Timeframe.M5,
            Timeframe.M15,
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
            Timeframe.W1,
        }
    ),
    asset_classes=frozenset(
        {
            AssetClass.EQUITY,
            AssetClass.ETF,
            AssetClass.INDEX,
            AssetClass.FX_SPOT,
            AssetClass.CFD,
            AssetClass.CRYPTO,
        }
    ),
    max_bars_per_request=5000,
    rate_limit_per_minute=8,
    has_point_in_time=False,
    adjusts_corporate_actions=True,
    is_broker_specific=False,
    supports_volume=True,
    notes=(
        "Ofrece datos intradía: 5 minutos, 15 minutos y 1 hora.",
        "Plan gratuito limitado a 8 peticiones por minuto y ~800 al día.",
        "Requiere clave gratuita en la variable TWELVEDATA_API_KEY.",
        "El histórico intradía del plan gratuito es corto: semanas, no años.",
    ),
)

_INTERVALO = {
    Timeframe.M5: "5min",
    Timeframe.M15: "15min",
    Timeframe.H1: "1h",
    Timeframe.H4: "4h",
    Timeframe.D1: "1day",
    Timeframe.W1: "1week",
}


class TwelveDataProvider:
    """Adaptador de solo lectura para Twelve Data.

    Args:
        api_key: Clave de acceso. Si no se indica, se toma de la variable de
            entorno `TWELVEDATA_API_KEY`.
        fetcher: Función `(url) -> texto`. Inyectada para poder probar el
            adaptador sin red.
    """

    def __init__(self, api_key: str | None = None, fetcher=None) -> None:
        # `None` significa "búscala en el entorno". Una cadena vacía significa
        # "sin clave", de forma explícita, y NO debe caer al entorno: si lo
        # hiciera, el comportamiento del objeto dependería de cómo esté
        # configurada la máquina, y las pruebas dejarían de ser deterministas.
        # Detectado en la v1.9.1, cuando CA-90 falló en un equipo que sí tenía
        # la clave configurada.
        if api_key is None:
            self._api_key = os.environ.get(CLAVE_ENV, "")
        else:
            self._api_key = api_key
        self._fetch = fetcher or _descargar

    @property
    def capabilities(self) -> ProviderCapabilities:
        return TWELVEDATA_CAPABILITIES

    @property
    def is_configured(self) -> bool:
        """Si hay clave disponible. Sin ella el proveedor no puede usarse."""
        return bool(self._api_key)

    def fetch_bars(
        self,
        provider_symbol: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> FetchResult:
        """Descarga barras de Twelve Data en el rango [start, end).

        Args:
            provider_symbol: Símbolo del proveedor. Ej. `SPX`, `EUR/USD`,
                `XAU/USD`.
            canonical_symbol: Símbolo canónico interno.
            timeframe: Resolución solicitada, incluidas las intradía.
            start: Inicio UTC inclusivo.
            end: Fin UTC exclusivo.

        Returns:
            Barras ordenadas con su procedencia.

        Raises:
            ProviderError: Sin clave configurada, timeframe no soportado o
                fallo de descarga.
            ProviderContractError: Respuesta con estructura inesperada.
        """
        if not self.is_configured:
            raise ProviderError(
                f"Falta la clave de Twelve Data. Obtén una gratuita en "
                f"twelvedata.com y guárdala en la variable de entorno "
                f"{CLAVE_ENV}."
            )
        if timeframe not in self.capabilities.timeframes:
            raise ProviderError(
                f"Twelve Data no soporta el timeframe {timeframe.value}"
            )

        params = {
            "symbol": provider_symbol,
            "interval": _INTERVALO[timeframe],
            "start_date": start.strftime("%Y-%m-%d %H:%M:%S"),
            "end_date": end.strftime("%Y-%m-%d %H:%M:%S"),
            "outputsize": str(self.capabilities.max_bars_per_request),
            "format": "JSON",
            "timezone": "UTC",
        }
        url = f"{API_URL}?{urllib.parse.urlencode({**params, 'apikey': self._api_key})}"

        try:
            payload = self._fetch(url)
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Fallo al descargar de Twelve Data: {exc}") from exc

        bars = self._parse(payload, canonical_symbol, timeframe, start, end)

        # La clave nunca entra en la procedencia: quedaría almacenada en la
        # base de datos y visible en cualquier informe.
        provenance = Provenance(
            source=DataSource.TWELVEDATA,
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
        """Convierte la respuesta JSON en barras canónicas."""
        try:
            datos = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProviderContractError(
                f"Respuesta no interpretable de Twelve Data: {exc}"
            ) from exc

        if isinstance(datos, dict) and datos.get("status") == "error":
            mensaje = datos.get("message", "sin detalle")
            # El agotamiento de cuota es recuperable esperando; un símbolo
            # inexistente no. Se distinguen para no reintentar en vano.
            if "limit" in mensaje.lower() or "credits" in mensaje.lower():
                raise ProviderError(f"Cuota agotada en Twelve Data: {mensaje}")
            raise ProviderContractError(f"Twelve Data rechazó la petición: {mensaje}")

        valores = datos.get("values") if isinstance(datos, dict) else None
        if not valores:
            return []

        bars: list[Bar] = []
        for fila in valores:
            try:
                ts = datetime.strptime(
                    fila["datetime"],
                    "%Y-%m-%d %H:%M:%S" if " " in fila["datetime"] else "%Y-%m-%d",
                ).replace(tzinfo=timezone.utc)

                if timeframe in (Timeframe.D1, Timeframe.W1):
                    ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)

                volumen = fila.get("volume")
                bar = Bar(
                    symbol=canonical_symbol,
                    timeframe=timeframe,
                    ts=ts,
                    source=DataSource.TWELVEDATA,
                    open=_dec(fila["open"]),
                    high=_dec(fila["high"]),
                    low=_dec(fila["low"]),
                    close=_dec(fila["close"]),
                    volume=_dec(volumen) if volumen not in (None, "", "0") else None,
                )
            except (KeyError, TypeError, ValueError, ValidationError):
                # Una fila corrupta se descarta; abortar el lote entero por
                # una sola impediría toda ingesta.
                continue
            if start <= bar.ts < end:
                bars.append(bar)

        bars.sort(key=lambda b: b.ts)
        return bars


def _dec(valor) -> Decimal:
    """Convierte a Decimal redondeando a seis decimales.

    El redondeo evita que el ruido de los últimos decimales haga cambiar el
    hash de contenido entre descargas idénticas y dispare falsas alarmas de
    revisión del proveedor.
    """
    return Decimal(str(round(float(valor), 6)))


def _descargar(url: str) -> str:
    """Descarga real. Se separa para poder inyectar una versión de prueba."""
    peticion = urllib.request.Request(
        url, headers={"User-Agent": "QQ-Quant-OS/1.6"}
    )
    with urllib.request.urlopen(peticion, timeout=30) as respuesta:
        return respuesta.read().decode("utf-8", errors="replace")
