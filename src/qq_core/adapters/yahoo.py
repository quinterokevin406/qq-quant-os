"""Adaptador de Yahoo Finance.

POR QUÉ EXISTE ESTE ADAPTADOR
------------------------------
Stooq comenzó a bloquear las descargas automáticas devolviendo una página de
verificación de navegador en lugar de datos. El adaptador de Stooq lo detectó
correctamente —rechazó la respuesta en vez de guardar basura— y reportó el
fallo instrumento por instrumento.

Sustituirlo requirió escribir esta clase. Ningún otro módulo del sistema se
modificó: ni el servicio de ingesta, ni el almacenamiento, ni las estrategias,
ni el panel. Es la validación práctica del requisito "cambiar proveedores sin
modificar el resto del sistema".

DEPENDENCIA
-----------
Usa la librería `yfinance`, que encapsula los detalles cambiantes del API de
Yahoo. Se aísla aquí, en la frontera: ningún otro fichero del proyecto la
importa. Si Yahoo cambia o `yfinance` deja de mantenerse, el impacto queda
confinado a este archivo.

LIMITACIONES DECLARADAS
-----------------------
Yahoo no es un proveedor de grado institucional. Su histórico se revisa, sus
datos de volumen en índices son poco fiables y no ofrece point-in-time. Es
adecuado para investigación exploratoria; para producción hay que sustituirlo
por un proveedor de pago. Todo ello queda declarado en `capabilities` para que
el Módulo 02 pueda actuar en consecuencia.
"""

from __future__ import annotations

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

YAHOO_CAPABILITIES = ProviderCapabilities(
    source=DataSource.YAHOO,
    timeframes=frozenset({Timeframe.D1, Timeframe.W1}),
    asset_classes=frozenset(
        {
            AssetClass.EQUITY,
            AssetClass.ETF,
            AssetClass.INDEX,
            AssetClass.FX_SPOT,
            AssetClass.FUTURE,
            AssetClass.CFD,
        }
    ),
    max_bars_per_request=100_000,
    rate_limit_per_minute=None,
    has_point_in_time=False,
    adjusts_corporate_actions=True,
    is_broker_specific=False,
    supports_volume=True,
    notes=(
        "Proveedor gratuito sin garantía de servicio ni de calidad.",
        "El histórico se revisa sin aviso; vigilar el registro de revisiones.",
        "El volumen en índices no es fiable.",
        "No apto para producción: sustituir por proveedor de pago.",
    ),
)

_INTERVAL = {Timeframe.D1: "1d", Timeframe.W1: "1wk"}


class YahooProvider:
    """Adaptador de solo lectura para Yahoo Finance.

    Args:
        downloader: Función `(symbol, start, end, interval) -> lista de dicts`
            con claves date/open/high/low/close/volume. Inyectada para poder
            probar el adaptador sin red. Si es `None`, usa `yfinance`.
    """

    def __init__(self, downloader=None) -> None:
        self._downloader = downloader or _yfinance_download

    @property
    def capabilities(self) -> ProviderCapabilities:
        return YAHOO_CAPABILITIES

    def fetch_bars(
        self,
        provider_symbol: str,
        canonical_symbol: str,
        timeframe: Timeframe,
        start: datetime,
        end: datetime,
    ) -> FetchResult:
        """Descarga barras de Yahoo en el rango [start, end).

        Args:
            provider_symbol: Ticker de Yahoo. Ej. `^GSPC`, `EURUSD=X`.
            canonical_symbol: Símbolo canónico interno.
            timeframe: `D1` o `W1`.
            start: Inicio UTC inclusivo.
            end: Fin UTC exclusivo.

        Returns:
            Barras ordenadas con su procedencia.

        Raises:
            ProviderError: Timeframe no soportado o fallo de descarga.
            ProviderContractError: Datos con estructura o valores inválidos.
        """
        if timeframe not in self.capabilities.timeframes:
            raise ProviderError(f"Yahoo no soporta el timeframe {timeframe.value}")

        params = {
            "symbol": provider_symbol,
            "start": start.date().isoformat(),
            "end": end.date().isoformat(),
            "interval": _INTERVAL[timeframe],
        }

        try:
            filas = self._downloader(
                provider_symbol, start, end, _INTERVAL[timeframe]
            )
        except Exception as exc:  # noqa: BLE001
            raise ProviderError(f"Fallo al descargar de Yahoo: {exc}") from exc

        bars: list[Bar] = []
        for fila in filas:
            try:
                ts = fila["date"]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                else:
                    ts = ts.astimezone(timezone.utc)
                # Yahoo entrega la fecha de sesión; se normaliza a medianoche
                # UTC para cumplir la alineación de rejilla exigida por `Bar`.
                ts = ts.replace(hour=0, minute=0, second=0, microsecond=0)

                volumen = fila.get("volume")
                bar = Bar(
                    symbol=canonical_symbol,
                    timeframe=timeframe,
                    ts=ts,
                    source=DataSource.YAHOO,
                    open=_dec(fila["open"]),
                    high=_dec(fila["high"]),
                    low=_dec(fila["low"]),
                    close=_dec(fila["close"]),
                    volume=(
                        None
                        if volumen is None or volumen != volumen or volumen < 0
                        else _dec(volumen)
                    ),
                )
            except (KeyError, TypeError, ValueError, ValidationError) as exc:
                # Una fila corrupta no invalida el lote: se descarta y se
                # continúa. Yahoo emite ocasionalmente filas con NaN en días
                # sin negociación, y abortar por eso impediría toda ingesta.
                continue
            if start <= bar.ts < end:
                bars.append(bar)

        if filas and not bars:
            raise ProviderContractError(
                f"Yahoo devolvió {len(filas)} filas para '{provider_symbol}' "
                f"pero ninguna resultó válida en el rango solicitado"
            )

        bars.sort(key=lambda b: b.ts)
        provenance = Provenance(
            source=DataSource.YAHOO,
            provider_symbol=provider_symbol,
            ingested_at=datetime.now(timezone.utc),
            raw_payload_hash=hash_payload(
                f"{provider_symbol}|{len(bars)}|"
                f"{bars[0].ts if bars else ''}|{bars[-1].ts if bars else ''}|"
                f"{bars[-1].close if bars else ''}"
            ),
            request_params=params,
        )
        return FetchResult(bars=tuple(bars), provenance=provenance)


def _dec(value) -> Decimal:
    """Convierte a Decimal preservando el valor redondeado a 6 decimales.

    Se redondea deliberadamente: los flotantes de Yahoo arrastran ruido en el
    decimoquinto decimal que, sin redondear, haría que el hash de contenido
    cambiara entre descargas idénticas y disparara falsas alarmas de revisión.
    """
    return Decimal(str(round(float(value), 6)))


def _yfinance_download(symbol: str, start: datetime, end: datetime, interval: str):
    """Descarga real usando `yfinance`. Importada de forma perezosa.

    La importación se hace aquí y no arriba para que el núcleo pueda usarse y
    probarse sin tener `yfinance` instalado.
    """
    import yfinance as yf

    ticker = yf.Ticker(symbol)
    frame = ticker.history(
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        interval=interval,
        auto_adjust=False,
        actions=False,
    )
    if frame is None or frame.empty:
        return []

    frame = frame.reset_index()
    columnas = {c.lower(): c for c in frame.columns}
    col_fecha = columnas.get("date") or columnas.get("datetime")
    if col_fecha is None:
        return []

    filas = []
    for _, r in frame.iterrows():
        filas.append(
            {
                "date": r[col_fecha].to_pydatetime(),
                "open": r[columnas["open"]],
                "high": r[columnas["high"]],
                "low": r[columnas["low"]],
                "close": r[columnas["close"]],
                "volume": r.get(columnas.get("volume", ""), None),
            }
        )
    return filas
