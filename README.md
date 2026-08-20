# QQ Quant OS

Plataforma de investigación cuantitativa multiactivo. Sistema de solo lectura:
genera señales; **no envía órdenes**. La ejecución es manual en el bróker.

## Estado

| Módulo | Estado |
|---|---|
| 01 Data Engine | Implementado — pendiente validación con datos reales |
| 02–20 | No iniciados |

## Estructura

```
docs/
  adr/                      Architecture Decision Records
  modules/01-data-engine/   Diseño, justificación, riesgos, criterios
src/qq_core/                Núcleo: dominio, puertos, adaptadores
tests/                      43 tests, mapeados a criterios de aceptación
```

## Ejecutar las pruebas

```bash
pip install -e ".[dev]"
pytest -q
```

## Reglas no negociables

1. **`qq_core` nunca importa `MetaTrader5`.** Verificado por CA-10 en CI.
2. **No existe código capaz de enviar órdenes.** Verificado por CA-11 en CI.
3. **Los contratos `Instrument`, `Bar` y `Provenance` requieren ADR** para
   cualquier cambio incompatible.
4. **Ninguna barra se almacena sin procedencia.**
5. **El Data Engine no limpia datos.** Eso es el Módulo 02.

## Siguiente paso bloqueante

Auditar la especificación de símbolos de GBI: ¿son futuros de bolsa o CFDs?
La respuesta cambia el modelado y la viabilidad de las estrategias de carry y
term structure.
