# ADR-0001 — Modelo de instrumento como unión discriminada

- **Estado**: Aceptado
- **Fecha**: 2026-08-04
- **Afecta a**: `qq_core.domain.instrument` — contrato MAJOR

## Contexto

QQ Quant OS debe soportar diez clases de activo. Cada una tiene atributos
obligatorios distintos e incompatibles entre sí:

- Un futuro tiene vencimiento y multiplicador; una acción no.
- Una acción tiene ISIN y corporate actions; un futuro no.
- Un CFD tiene emisor y financiación overnight; ninguno de los otros.
- Una serie macro tiene retardo de revisión y no es negociable.

## Decisión

Modelar `Instrument` como una unión discriminada por `asset_class`, con una
clase base mínima (`InstrumentBase`) que contiene solo lo universalmente
verdadero, y una subclase por clase de activo con sus campos obligatorios.

`CFD` es una clase distinta de `Future`, no una variante.

## Consecuencias

**Positivas**

- El estado inválido es inconstruible: un `Future` sin `expiry` falla en
  validación, no en producción.
- Una estrategia de term structure no puede recibir un CFD por accidente.
- Añadir una clase de activo es aditivo (MINOR), no invasivo.

**Negativas**

- El código consumidor debe hacer `match` sobre `asset_class`. Aceptado
  deliberadamente: obliga a decidir explícitamente el comportamiento por clase.
- La persistencia usa JSONB para `spec`, con restricciones CHECK por clase.

## Alternativas rechazadas

1. **Clase única con campos opcionales.** Convierte errores de modelado en
   `None` silenciosos que se propagan al cálculo de PnL.
2. **Tabla por clase de activo en SQL.** Multiplica JOINs sin aportar
   integridad real; la validación fuerte ya ocurre en Pydantic.
3. **CFD como `Future` con flag.** Permite que una estrategia de carry acepte
   un CFD, midiendo el spread del bróker en lugar de la curva del mercado.

## Revisión

Cualquier cambio a la jerarquía de `Instrument` requiere un ADR nuevo que
supersede a este, más un plan de migración del histórico ya ingestado.
