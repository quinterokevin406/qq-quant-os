# Contrato del gateway MT5 — estado de cuenta

Documento para el equipo que implemente el servicio. No hace falta conocer el
resto del sistema.

## Qué hay que construir

Un servicio HTTP corriendo en la máquina Windows donde está el terminal
MetaTrader 5, que exponga tres rutas de sólo lectura.

**No debe poder enviar órdenes.** El sistema completo está diseñado para no
ejecutar operaciones, y esa restricción se verifica automáticamente en CI. Un
gateway con capacidad de ejecución la rompería.

## Rutas

### GET /account

```json
{
  "balance": 10000.0,
  "equity": 10240.5,
  "margin": 1200.0,
  "margin_free": 9040.5,
  "margin_level": 853.4,
  "profit": 240.5,
  "leverage": 30,
  "currency": "USD"
}
```

Origen: `mt5.account_info()`. Todos los importes en divisa de la cuenta.

### GET /positions

```json
[
  {
    "ticket": "123456",
    "symbol": "US500",
    "type": "buy",
    "volume": 0.1,
    "price_open": 5000.0,
    "price_current": 5030.0,
    "sl": 4900.0,
    "tp": null,
    "swap": -3.2,
    "profit": 30.0,
    "time": "2026-08-18T10:00:00Z"
  }
]
```

Origen: `mt5.positions_get()`. `time` en ISO 8601 con zona horaria explícita.
`sl` y `tp` valen `null` si no están puestos, nunca `0.0` — un cero es un
precio válido y se interpretaría como stop en cero.

### GET /symbol/{symbol}/financing

```json
{
  "swap_long": -13.36,
  "swap_short": 4.49,
  "swap_mode": "interest_current",
  "swap_rollover_3days": 3,
  "contract_size": 1.0,
  "point_value": 1.0
}
```

Origen: `mt5.symbol_info(symbol)`.

## El punto donde más se falla

**`swap_mode` es obligatorio y NO debe traducirse.**

MT5 expresa el swap en unidades distintas según el símbolo: puntos, divisa del
margen, porcentaje, o tipo de interés anual. El campo `SYMBOL_SWAP_MODE` dice
cuál.

Devolver el número sin el modo hace imposible interpretarlo. Convertirlo mal
produce errores de un orden de magnitud en el coste de mantener posiciones, y
ese coste es el hallazgo económico central de este proyecto: el bróker cobra
un 13,36% anual por mantener US500 comprado y paga un 4,49% por mantenerlo
vendido.

Devolver el valor tal como lo da el terminal, sin convertir. La conversión se
hace en el sistema, donde está documentada y probada.

`swap_rollover_3days` indica el día con cargo triple (0 = lunes, 3 = jueves).

## Cómo verificar que cumple

```
pytest tests/test_contrato_cuenta.py
```

Sustituir el `fetcher_simulado` por un cliente real apuntando al servicio.
Si las pruebas pasan, el contrato se cumple.

## Errores

Devolver códigos HTTP estándar. El sistema distingue entre "no configurado"
(no hay dirección de gateway) e "inaccesible" (hay dirección pero no responde).
Nunca devolver `200` con ceros: el sistema prefiere un error visible a un dato
falso.
