"""Implementaciones del puerto `AccountProvider`.

Dos proveedores:

`ManualAccountProvider`  Operativo hoy. El operador introduce balance y equity
                         en el perfil de cuenta y el motor trabaja con eso.

`MT5AccountProvider`     Estructura completa, sin conexión. Pensado para que un
                         equipo técnico implemente el gateway al otro lado sin
                         tener que entender el resto del sistema.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from qq_core.ports.account_provider import (
    AccountDataUnavailable,
    AccountProviderError,
    AccountState,
    BrokerPosition,
    SymbolFinancing,
)

REQUIERE_GATEWAY = (
    "gateway MT5 conectado al terminal del bróker. Ver "
    "docs/adr/ADR-0004-account-provider-port.md"
)


class ManualAccountProvider:
    """Estado de cuenta introducido a mano por el operador.

    Sirve para que todo el motor de portafolio funcione y sea verificable antes
    de que exista el gateway. Lo que NO puede hacer es inventarse el margen ni
    las posiciones del bróker: esos métodos fallan con un mensaje explícito.

    El operador puede introducir el margen manualmente si lo consulta en su
    terminal, y en ese caso sí se usa. Lo que nunca ocurre es que el sistema lo
    estime por su cuenta.
    """

    def __init__(
        self,
        balance: Decimal,
        equity: Decimal | None = None,
        currency: str = "USD",
        margin_used: Decimal | None = None,
        leverage: int | None = None,
    ) -> None:
        """Inicializa el proveedor manual.

        Args:
            balance: Saldo de la cuenta.
            equity: Saldo más flotante. Si no se indica, se asume igual al
                balance, lo que equivale a no tener posiciones abiertas.
            currency: Divisa de la cuenta.
            margin_used: Margen ocupado, si el operador lo consultó y lo
                introdujo. `None` significa desconocido, NO cero.
            leverage: Apalancamiento del bróker, si se conoce.
        """
        if balance < 0:
            raise ValueError("el balance no puede ser negativo")

        self._balance = Decimal(balance)
        self._equity = Decimal(equity) if equity is not None else Decimal(balance)
        self._currency = currency
        self._margin_used = Decimal(margin_used) if margin_used is not None else None
        self._leverage = leverage

    @property
    def name(self) -> str:
        return "manual"

    @property
    def is_live(self) -> bool:
        return False

    def fetch_account_state(self) -> AccountState:
        """Devuelve el estado introducido manualmente."""
        margen_libre = (
            self._equity - self._margin_used if self._margin_used is not None else None
        )
        nivel = None
        if self._margin_used is not None and self._margin_used > 0:
            nivel = (self._equity / self._margin_used) * Decimal(100)

        return AccountState(
            as_of=datetime.now(timezone.utc),
            currency=self._currency,
            balance=self._balance,
            equity=self._equity,
            margin_used=self._margin_used,
            margin_free=margen_libre,
            margin_level_pct=nivel,
            floating_pnl=self._equity - self._balance,
            realized_pnl=None,
            leverage=self._leverage,
            is_live=False,
            source=self.name,
        )

    def fetch_positions(self) -> list[BrokerPosition]:
        """No disponible: el proveedor manual no conoce el libro del bróker.

        Raises:
            AccountDataUnavailable: Siempre.
        """
        raise AccountDataUnavailable("positions", REQUIERE_GATEWAY)

    def fetch_financing(self, symbol: str) -> SymbolFinancing:
        """No disponible: medir el swap exige leer el terminal.

        Raises:
            AccountDataUnavailable: Siempre.
        """
        raise AccountDataUnavailable(f"financing[{symbol}]", REQUIERE_GATEWAY)


class MT5AccountProvider:
    """Estado de cuenta leído del gateway MT5.

    NO IMPLEMENTADO. Esta clase define el contrato que debe cumplir el gateway;
    el servicio HTTP al otro lado no existe todavía.

    QUÉ TIENE QUE CONSTRUIR EL EQUIPO TÉCNICO
    -------------------------------------------
    Un servicio HTTP, corriendo en la máquina Windows donde está el terminal,
    que exponga tres rutas. El contrato completo, con ejemplos de respuesta,
    está en `docs/gateway/CONTRATO-CUENTA.md`.

        GET /account
            -> {"balance": 10000.0, "equity": 10240.5, "margin": 1200.0,
                "margin_free": 9040.5, "margin_level": 853.4,
                "profit": 240.5, "leverage": 30, "currency": "USD"}

        GET /positions
            -> [{"ticket": "123", "symbol": "US500", "type": "buy",
                 "volume": 0.1, "price_open": 5000.0, "price_current": 5030.0,
                 "sl": 4900.0, "tp": null, "swap": -3.2, "profit": 30.0,
                 "time": "2026-08-18T10:00:00Z"}]

        GET /symbol/{symbol}/financing
            -> {"swap_long": -13.36, "swap_short": 4.49,
                "swap_mode": "interest_current", "swap_rollover_3days": 3,
                "contract_size": 1.0, "point_value": 1.0}

    AVISO PARA QUIEN LO IMPLEMENTE
    -------------------------------
    El campo `swap_mode` es el que más errores causa. MT5 expresa el swap en
    unidades distintas según el símbolo: puntos, divisa del margen, o tipo de
    interés anual. Devolver el número sin el modo hace imposible convertirlo, y
    convertirlo mal produce errores de un orden de magnitud en el coste de
    mantener posiciones. Devolver siempre el valor de `SYMBOL_SWAP_MODE` tal
    como lo da el terminal, sin traducir.

    Las pruebas de `tests/test_contrato_cuenta.py` verifican el cumplimiento del
    contrato usando un cliente simulado. Ejecutarlas contra la implementación
    real dirá si cumple.
    """

    def __init__(self, base_url: str = "", timeout: float = 10.0, fetcher=None) -> None:
        """Inicializa el proveedor.

        Args:
            base_url: Dirección del gateway. Vacío significa no configurado.
            timeout: Tiempo máximo de espera en segundos.
            fetcher: Función de descarga inyectable, para pruebas.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._fetcher = fetcher or self._http_get

    @property
    def name(self) -> str:
        return "mt5_gateway"

    @property
    def is_live(self) -> bool:
        return bool(self._base_url)

    @property
    def is_configured(self) -> bool:
        return bool(self._base_url)

    def _http_get(self, url: str) -> str:
        peticion = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(peticion, timeout=self._timeout) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise AccountProviderError(f"gateway MT5 inaccesible: {exc}") from exc

    def _get_json(self, ruta: str) -> Any:
        if not self._base_url:
            raise AccountDataUnavailable(ruta, REQUIERE_GATEWAY)
        crudo = self._fetcher(f"{self._base_url}{ruta}")
        try:
            return json.loads(crudo)
        except json.JSONDecodeError as exc:
            raise AccountProviderError(f"respuesta no es JSON válido: {exc}") from exc

    def fetch_account_state(self) -> AccountState:
        """Lee el estado de la cuenta del gateway."""
        d = self._get_json("/account")
        return AccountState(
            as_of=datetime.now(timezone.utc),
            currency=str(d.get("currency", "USD")),
            balance=Decimal(str(d["balance"])),
            equity=Decimal(str(d["equity"])),
            margin_used=Decimal(str(d["margin"])),
            margin_free=Decimal(str(d["margin_free"])),
            margin_level_pct=Decimal(str(d.get("margin_level", 0))),
            floating_pnl=Decimal(str(d.get("profit", 0))),
            realized_pnl=None,
            leverage=int(d.get("leverage", 0)) or None,
            is_live=True,
            source=self.name,
        )

    def fetch_positions(self) -> list[BrokerPosition]:
        """Lee las posiciones abiertas del gateway."""
        filas = self._get_json("/positions")
        return [
            BrokerPosition(
                ticket=str(p["ticket"]),
                symbol=str(p["symbol"]),
                direction="long" if str(p["type"]).lower() in ("buy", "0") else "short",
                volume=Decimal(str(p["volume"])),
                open_price=Decimal(str(p["price_open"])),
                current_price=Decimal(str(p["price_current"])),
                stop_loss=Decimal(str(p["sl"])) if p.get("sl") else None,
                take_profit=Decimal(str(p["tp"])) if p.get("tp") else None,
                swap_accumulated=Decimal(str(p.get("swap", 0))),
                profit=Decimal(str(p.get("profit", 0))),
                opened_at=datetime.fromisoformat(
                    str(p["time"]).replace("Z", "+00:00")
                ),
            )
            for p in filas
        ]

    def fetch_financing(self, symbol: str) -> SymbolFinancing:
        """Lee el coste de financiación medido de un símbolo."""
        d = self._get_json(f"/symbol/{symbol}/financing")
        return SymbolFinancing(
            symbol=symbol,
            swap_long=Decimal(str(d["swap_long"])),
            swap_short=Decimal(str(d["swap_short"])),
            swap_mode=str(d["swap_mode"]),
            swap_rollover_3days=int(d.get("swap_rollover_3days", 3)),
            contract_size=Decimal(str(d.get("contract_size", 1))),
            point_value=Decimal(str(d.get("point_value", 1))),
            measured_at=datetime.now(timezone.utc),
        )
