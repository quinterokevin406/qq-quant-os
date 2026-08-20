# ADR-0006 — Corrección de ADR-0003 tras auditar el terminal

Fecha: 2026-08-19
Estado: aceptado
Sustituye parcialmente a: ADR-0003

## Contexto

ADR-0003 afirmaba que GBE Global sólo ofrece CFDs, que no hay futuros con curva
utilizable, y que el coste de financiación de US500 es -13,36% anual.

El 2026-08-19 se auditó el terminal con `scripts/medir_swaps.py` y con la
ventana de símbolos. Tres de esas afirmaciones son incorrectas o han caducado.

## Correcciones

### 1. Sí hay futuros

Existen `US500.U26` y `USTEC.U26`, futuros reales con vencimiento septiembre
2026: tamaño de contrato 50 (el del E-mini S&P) y swap desactivado, como
corresponde a un futuro.

**Pero la conclusión operativa de ADR-0003 se mantiene:** sólo hay un
vencimiento vivo por subyacente, y con "Show expired contracts" activado no
aparecen vencimientos anteriores. Sin al menos dos puntos simultáneos no hay
curva que medir. **Carry y Term Structure siguen bloqueadas**, por este motivo y
no por el que decía ADR-0003.

Se puede construir hacia adelante: guardando cada vencimiento antes de que
expire, en un año habría dos o tres puntos de curva.

### 2. El universo del bróker es mucho mayor de lo documentado

El árbol de símbolos incluye Forex, Equities US, Equities EU, Equities HK,
Bitcoin, Ethereum, Litecoin, Ripple, Energies, Metals exotic, Indices II y
JPN225. En total 1.144 símbolos.

ADR-0003 llevó a descartar como fuera de alcance las secciones de la
especificación relativas a acciones, ETFs y cripto. **Esa conclusión era
incorrecta.**

Esto NO implica ampliar el universo. Cada instrumento nuevo son 9 ensayos más y
sube el listón de la corrección por selección múltiple. Ampliar sin un motivo
fuerte empeora las probabilidades de validar lo que ya existe. Se documenta la
disponibilidad, no se actúa sobre ella.

### 3. El coste de US500 ha cambiado

| Medición | Valor comprado |
|---|---|
| 2026-08-06 (ADR-0003) | -13,36% |
| 2026-08-19 | **-8,63%** |

No es un error de medición: los swaps siguen a los tipos de interés. **Las
cifras de financiación caducan** y hay que remedirlas periódicamente.

Consecuencia en pruebas: CA-21 fijaba el valor -13,4% del catálogo y falló al
remedir. Se reescribió para verificar la conversión aritmética, no la cifra
vigente. Fijar en una prueba un valor que el mundo puede cambiar convierte una
medición legítima en un fallo de CI.

## Hallazgo adicional: las divisas operaban sin coste

`FxSpot` no tenía campo `financing`, y `financing_from_instrument` devolvía
`None` para todo lo que no fuera CFD. **Todos los backtests de divisas se
ejecutaron sin ningún coste de financiación.**

No era una aproximación conservadora: regalaba entre 0,8 y 2,7 puntos anuales a
los pares comprados que sí cuestan dinero. Corregido en v1.10, con CA-21c y
CA-22b para impedir que vuelva a ocurrir.

## Error de la estimación del -9%

| Grupo | Coste real comprado | Error del -9% |
|---|---|---|
| Divisas | -2,7% a +2,7% | 6 a 12 puntos |
| Petróleo | +4,9% y +19,1% | 14 a 28 puntos |
| Metales | -5,2% y -8,4% | aceptable |
| Índices US/DE | -7,9% a -10,0% | aceptable |
| Índices euro | -15,2% | 6 puntos |

El petróleo comprado **cobra**: es carry de backwardation, y el backtest lo
trataba como un coste del 9%.

## Estado

17 de 23 instrumentos con coste medido. Los 6 restantes (US2000, UK100, F40,
SWI20, HK50, AUS200) no estaban visibles en el terminal al medir y conservan la
estimación, declarada como tal.

## Consecuencia inmediata

Los 207 backtests y el informe de validación del Módulo 05 se calcularon con
costes incorrectos. **Hay que rehacerlos.**
