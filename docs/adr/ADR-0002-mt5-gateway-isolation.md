# ADR-0002 — Aislamiento de MetaTrader5 tras un gateway

- **Estado**: Aceptado
- **Fecha**: 2026-08-04
- **Afecta a**: topología de despliegue, CI, `qq_core.adapters.mt5_gateway`

## Contexto

El paquete `MetaTrader5` de Python es una dependencia nativa que solo funciona
en Windows y requiere un terminal MT5 instalado, abierto y con sesión iniciada.
El stack del proyecto incluye Docker, y el equipo necesita CI reproducible.

Importar `MetaTrader5` desde el núcleo ataría toda la plataforma —incluidos los
tests unitarios— a un host Windows con el terminal corriendo.

## Decisión

Un proceso separado `mt5-gateway` corre nativamente en Windows junto al
terminal, expone un HTTP mínimo en `127.0.0.1` y es el ÚNICO componente que
importa la librería nativa. El núcleo habla con él por HTTP.

El resto de la plataforma corre en contenedores Linux sobre WSL2 en el mismo
host Windows.

El gateway NO expone endpoints de ejecución. Un test del CI escanea el árbol de
`qq_core` en busca de identificadores de ejecución de MT5 y falla si aparecen.

## Consecuencias

**Positivas**

- El núcleo es portable, testeable sin MT5, y desplegable en cualquier sitio.
- La única dependencia no portable queda confinada a ~300 líneas reemplazables.
- La imposibilidad de enviar órdenes es estructural, no una convención.

**Negativas**

- Un proceso más que operar y monitorizar.
- Un salto de red local por petición. Irrelevante: la ingesta es batch.
- El gateway es punto único de fallo para datos de GBI. Mitigado con
  reanudación por watermark.

## Alternativas rechazadas

1. **Importar `MetaTrader5` en el núcleo.** Ver Contexto.
2. **Todo el sistema nativo en Windows sin contenedores.** Funciona hoy;
   convierte en rewrite cualquier movimiento futuro a la nube o a un equipo
   con Macs.
3. **Exportar CSV desde MT5 manualmente.** Sin trazabilidad, sin
   automatización, sin reanudación.
