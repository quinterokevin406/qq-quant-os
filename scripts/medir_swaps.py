"""Mide el coste real de financiación de los 23 instrumentos en el terminal MT5.

POR QUÉ EXISTE
--------------
Hoy 22 de los 23 instrumentos usan una estimación del -9% anual. El único
medido, US500, resultó ser -13,36% comprado y +4,49% vendido. Esa diferencia no
es un detalle: el coste de financiación entra directamente en el rendimiento de
cada backtest, y por tanto decide qué combinaciones sobreviven a la validación
estadística.

Mientras 22 instrumentos usen un número inventado, el informe del Módulo 05
está construido sobre datos que sabemos incorrectos.

POR QUÉ ESTE SCRIPT ESTÁ FUERA DE `qq_core`
---------------------------------------------
Importa `MetaTrader5`, que sólo funciona en Windows. ADR-0002 establece que el
núcleo del sistema nunca depende de esa librería. Este script es una
herramienta de medición puntual, no parte del sistema: se ejecuta a mano, deja
un fichero, y el sistema lee ese fichero.

EL CAMPO QUE MÁS ERRORES CAUSA
-------------------------------
`SYMBOL_SWAP_MODE`. MT5 expresa el swap en unidades distintas según el símbolo:
puntos, divisa del margen, porcentaje anual, o tipo de interés. Un mismo número
"-13.36" significa cosas completamente distintas según el modo.

Este script NO convierte a ciegas. Registra el modo, aplica la conversión sólo
cuando sabe hacerla con certeza, y marca como `REVISAR_A_MANO` los casos donde
la conversión no es inequívoca. Es preferible a producir 23 cifras de las
cuales algunas estén equivocadas en un orden de magnitud sin que nadie lo note.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import MetaTrader5 as mt5
except ImportError:
    print("Falta la librería. Ejecuta:  pip install MetaTrader5")
    print("Sólo funciona en Windows y con MetaTrader 5 abierto.")
    raise SystemExit(1)

# Modos de swap de MT5. El número es el valor de SYMBOL_SWAP_MODE.
MODOS = {
    0: ("DESACTIVADO", "sin swap"),
    1: ("PUNTOS", "en puntos del símbolo"),
    2: ("MONEDA_SIMBOLO", "en divisa base del símbolo"),
    3: ("PORCENTAJE_INTERES", "porcentaje anual sobre el nocional"),
    4: ("MONEDA_MARGEN", "en divisa del margen"),
    5: ("MONEDA_DEPOSITO", "en divisa de la cuenta"),
    6: ("INTERES_ACTUAL", "tipo de interés anual, precio actual"),
    7: ("INTERES_APERTURA", "tipo de interés anual, precio de apertura"),
    8: ("REAPERTURA_ACTUAL", "reapertura a precio de cierre"),
    9: ("REAPERTURA_BID", "reapertura a precio bid"),
}

# Los modos 3, 6 y 7 ya vienen expresados como porcentaje anual: se pueden
# usar directamente. El resto exigen conocer precio, tamaño de contrato y
# divisa, y la conversión deja de ser inequívoca. Esos se marcan para revisión.
MODOS_YA_ANUALES = {3, 6, 7}


def main() -> int:
    if not mt5.initialize():
        print(f"No se pudo conectar con MetaTrader 5: {mt5.last_error()}")
        print("Comprueba que el terminal esté abierto y con sesión iniciada.")
        return 1

    cuenta = mt5.account_info()
    if cuenta is None:
        print("Terminal abierto pero sin cuenta conectada.")
        mt5.shutdown()
        return 1

    print()
    print("=" * 78)
    print("  MEDICIÓN DE COSTES DE FINANCIACIÓN")
    print(f"  Cuenta {cuenta.login} · {cuenta.server} · divisa {cuenta.currency}")
    print("=" * 78)
    print()

    # Se leen TODOS los símbolos visibles del terminal. El emparejamiento con
    # el catálogo se hace después, a mano, porque los nombres del bróker no
    # siempre coinciden con los canónicos del sistema.
    simbolos = mt5.symbols_get()
    if not simbolos:
        print("El terminal no devuelve símbolos.")
        mt5.shutdown()
        return 1

    filas = []
    print(f"  {'SÍMBOLO':<14} {'LARGO':>10} {'CORTO':>10}  {'MODO':<20} {'3D':>3}")
    print(f"  {'-' * 74}")

    for s in simbolos:
        if not s.visible:
            continue

        modo_num = int(s.swap_mode)
        modo_nombre, modo_desc = MODOS.get(modo_num, (f"MODO_{modo_num}", "desconocido"))

        directo = modo_num in MODOS_YA_ANUALES
        fila = {
            "simbolo_broker": s.name,
            "swap_long_crudo": float(s.swap_long),
            "swap_short_crudo": float(s.swap_short),
            "swap_mode_num": modo_num,
            "swap_mode": modo_nombre,
            "swap_mode_desc": modo_desc,
            "swap_rollover_3days": int(s.swap_rollover3days),
            "contract_size": float(s.trade_contract_size),
            "digits": int(s.digits),
            "point": float(s.point),
            "precio_actual": float(s.bid) if s.bid else None,
            "moneda_base": s.currency_base,
            "moneda_beneficio": s.currency_profit,
            "moneda_margen": s.currency_margin,
            # Sólo se rellena cuando la conversión es inequívoca.
            "swap_long_anual_pct": float(s.swap_long) if directo else None,
            "swap_short_anual_pct": float(s.swap_short) if directo else None,
            "estado": "MEDIDO" if directo else "REVISAR_A_MANO",
        }
        filas.append(fila)

        marca = "" if directo else "  <-- revisar"
        print(
            f"  {s.name:<14} {s.swap_long:>10.4f} {s.swap_short:>10.4f}  "
            f"{modo_nombre:<20} {s.swap_rollover3days:>3}{marca}"
        )

    mt5.shutdown()

    salida = Path("swaps_medidos.json")
    salida.write_text(
        json.dumps(
            {
                "medido_en": datetime.now(timezone.utc).isoformat(),
                "cuenta": cuenta.login,
                "servidor": cuenta.server,
                "divisa_cuenta": cuenta.currency,
                "instrumentos": filas,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    medidos = sum(1 for f in filas if f["estado"] == "MEDIDO")
    revisar = len(filas) - medidos

    print(f"  {'-' * 74}")
    print(f"  Símbolos leídos: {len(filas)}")
    print(f"  Convertibles directamente a % anual: {medidos}")
    print(f"  Requieren conversión manual: {revisar}")
    print()
    print(f"  Guardado en: {salida.resolve()}")
    print()
    if revisar:
        print("  Los marcados como REVISAR_A_MANO usan un modo de swap cuya")
        print("  conversión a porcentaje anual no es inequívoca. Convertirlos")
        print("  a ciegas produciría errores de un orden de magnitud, así que")
        print("  el script prefiere no hacerlo.")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
