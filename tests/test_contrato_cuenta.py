"""Pruebas de contrato del puerto `AccountProvider`.

PARA QUIÉN ES ESTE ARCHIVO
---------------------------
Para el equipo técnico que vaya a implementar el gateway MT5. Estas pruebas
definen qué debe cumplir el servicio HTTP del otro lado. Ejecutarlas contra la
implementación real dice si cumple el contrato, sin necesidad de entender el
resto del sistema.

Las pruebas usan un cliente simulado: no requieren MetaTrader ni Windows, y se
ejecutan en cualquier máquina.

LA PROPIEDAD MÁS IMPORTANTE QUE VERIFICAN
-------------------------------------------
`test_proveedor_manual_no_inventa_el_margen`. Un proveedor que no puede obtener
un dato debe FALLAR, nunca devolver cero. Si el margen desconocido se mostrara
como 0%, alguien abriría posiciones creyendo tener capacidad libre.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from qq_core.adapters.account_providers import ManualAccountProvider, MT5AccountProvider
from qq_core.ports.account_provider import (
    AccountDataUnavailable,
    AccountProvider,
    AccountState,
)

RESPUESTA_CUENTA = {
    "balance": 10000.0,
    "equity": 10240.5,
    "margin": 1200.0,
    "margin_free": 9040.5,
    "margin_level": 853.4,
    "profit": 240.5,
    "leverage": 30,
    "currency": "USD",
}

RESPUESTA_POSICIONES = [
    {
        "ticket": "123456",
        "symbol": "US500",
        "type": "buy",
        "volume": 0.1,
        "price_open": 5000.0,
        "price_current": 5030.0,
        "sl": 4900.0,
        "tp": None,
        "swap": -3.2,
        "profit": 30.0,
        "time": "2026-08-18T10:00:00Z",
    }
]

RESPUESTA_FINANCIACION = {
    "swap_long": -13.36,
    "swap_short": 4.49,
    "swap_mode": "interest_current",
    "swap_rollover_3days": 3,
    "contract_size": 1.0,
    "point_value": 1.0,
}


def fetcher_simulado(url: str) -> str:
    """Cliente HTTP simulado que responde según la ruta."""
    if url.endswith("/account"):
        return json.dumps(RESPUESTA_CUENTA)
    if url.endswith("/positions"):
        return json.dumps(RESPUESTA_POSICIONES)
    if "/financing" in url:
        return json.dumps(RESPUESTA_FINANCIACION)
    raise AssertionError(f"ruta no contemplada en el contrato: {url}")


# --------------------------------------------------------------------------- #
# CA-75 a CA-78: la regla de no inventar datos
# --------------------------------------------------------------------------- #


def test_proveedor_manual_no_inventa_el_margen() -> None:
    """CA-75: LA PRUEBA CRÍTICA del puerto.

    Un margen desconocido vale `None`, nunca cero. Un cero se mostraría como
    "margen utilizado: 0%" y alguien abriría posiciones creyendo tener toda la
    capacidad libre.
    """
    p = ManualAccountProvider(balance=Decimal("10000"))
    estado = p.fetch_account_state()

    assert estado.margin_used is None
    assert estado.margin_free is None
    assert "margin_used" in estado.missing_fields
    assert not estado.is_complete


def test_proveedor_manual_falla_al_pedir_posiciones_del_broker() -> None:
    """CA-76: no conocer el libro del bróker se declara, no se simula."""
    p = ManualAccountProvider(balance=Decimal("10000"))

    with pytest.raises(AccountDataUnavailable) as exc:
        p.fetch_positions()

    assert "gateway" in str(exc.value).lower()


def test_proveedor_manual_falla_al_pedir_financiacion_medida() -> None:
    """CA-77: medir el swap exige leer el terminal. No se estima aquí."""
    p = ManualAccountProvider(balance=Decimal("10000"))

    with pytest.raises(AccountDataUnavailable):
        p.fetch_financing("US500")


def test_gateway_sin_configurar_falla_en_lugar_de_devolver_ceros() -> None:
    """CA-78: un gateway sin dirección no finge estar conectado."""
    p = MT5AccountProvider(base_url="")

    assert not p.is_configured
    with pytest.raises(AccountDataUnavailable):
        p.fetch_account_state()


# --------------------------------------------------------------------------- #
# CA-79 a CA-82: el contrato del gateway
# --------------------------------------------------------------------------- #


def test_gateway_lee_el_estado_de_la_cuenta() -> None:
    """CA-79: contrato de GET /account."""
    p = MT5AccountProvider(base_url="http://localhost:8765", fetcher=fetcher_simulado)
    e = p.fetch_account_state()

    assert e.balance == Decimal("10000.0")
    assert e.equity == Decimal("10240.5")
    assert e.margin_used == Decimal("1200.0")
    assert e.leverage == 30
    assert e.is_live
    assert e.is_complete


def test_gateway_lee_las_posiciones_abiertas() -> None:
    """CA-80: contrato de GET /positions."""
    p = MT5AccountProvider(base_url="http://localhost:8765", fetcher=fetcher_simulado)
    posiciones = p.fetch_positions()

    assert len(posiciones) == 1
    pos = posiciones[0]
    assert pos.direction == "long"
    assert pos.symbol == "US500"
    assert pos.stop_loss == Decimal("4900.0")
    assert pos.take_profit is None
    # El swap acumulado es la medición real, no la estimación del catálogo.
    assert pos.swap_accumulated == Decimal("-3.2")


def test_gateway_devuelve_el_modo_de_swap_sin_traducir() -> None:
    """CA-81: `swap_mode` es obligatorio y NO debe convertirse en el gateway.

    MT5 expresa el swap en puntos, en divisa del margen o como tipo anual según
    el símbolo. Devolver el número sin el modo hace imposible interpretarlo, y
    convertirlo mal produce errores de un orden de magnitud en el coste de
    mantener posiciones.
    """
    p = MT5AccountProvider(base_url="http://localhost:8765", fetcher=fetcher_simulado)
    f = p.fetch_financing("US500")

    assert f.swap_mode == "interest_current"
    assert f.swap_long == Decimal("-13.36")
    assert f.swap_short == Decimal("4.49")
    # La asimetría estructural del bróker: comprado cuesta, vendido paga.
    assert f.swap_long < 0 < f.swap_short


def test_ambos_proveedores_cumplen_el_protocolo() -> None:
    """CA-82: las dos implementaciones satisfacen el puerto.

    Permite intercambiarlas sin tocar el motor de portafolio.
    """
    assert isinstance(ManualAccountProvider(balance=Decimal("1")), AccountProvider)
    assert isinstance(MT5AccountProvider(), AccountProvider)


def test_el_manual_si_acepta_margen_introducido_a_mano() -> None:
    """CA-83: si el operador lo consulta y lo introduce, sí se usa.

    Lo que nunca ocurre es que el sistema lo estime por su cuenta.
    """
    p = ManualAccountProvider(
        balance=Decimal("10000"),
        equity=Decimal("10200"),
        margin_used=Decimal("2000"),
    )
    e = p.fetch_account_state()

    assert e.margin_used == Decimal("2000")
    assert e.margin_free == Decimal("8200")
    assert e.margin_level_pct == Decimal("510")
    assert not e.is_live  # sigue sin ser dato en vivo
