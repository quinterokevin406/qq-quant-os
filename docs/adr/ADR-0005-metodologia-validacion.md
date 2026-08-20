# ADR-0005 — Métodos de corrección por selección múltiple

Fecha: 2026-08-18
Estado: aceptado

## Contexto

El sistema evalúa 9 estrategias sobre 23 instrumentos: 207 combinaciones.
Presentar la mejor sin corregir por el hecho de haber buscado entre 207 es
estadísticamente inválido.

Medición propia, en `tests/test_validacion.py` (CA-40): **207 series de puro
ruido aleatorio producen un Sharpe anualizado de 0,71 en la mejor de ellas.**
Sin corrección, eso pasaría por una estrategia decente.

## Decisión

Se implementan tres métodos complementarios y se exige superar los tres.

| Método | Pregunta que responde |
|---|---|
| SPA de Hansen con bootstrap por bloques | ¿La mejor de las 207 supera a no operar, sabiendo que buscamos entre 207? |
| Deflated Sharpe Ratio | ¿Este Sharpe concreto supera lo que saldría por azar? |
| PBO por validación cruzada combinatoria | ¿Elegir por backtest generaliza fuera de muestra? |

El SPA es la prueba principal y se evalúa primero.

## Por qué no Bonferroni ni Benjamini-Hochberg

Ambos suponen independencia o una estructura de dependencia concreta entre las
pruebas. Las 207 combinaciones están fuertemente correlacionadas: trece índices
bursátiles se mueven casi al unísono y varias estrategias comparten lógica.

Bonferroni sería tan conservador que rechazaría también lo que funcione.
Benjamini-Hochberg no es válido bajo esta dependencia.

El bootstrap por bloques del SPA remuestrea las 207 series conjuntamente,
preservando tanto la correlación entre ellas como la dependencia temporal
dentro de cada una, sin necesidad de modelar ninguna de las dos.

## Por qué SPA y no Reality Check de White

En el Reality Check, añadir candidatos muy malos a la búsqueda desplaza la
distribución nula y facilita pasar la prueba. El recentrado de Hansen descarta
esos candidatos inferiores y elimina el incentivo perverso.

## Limitaciones aceptadas y declaradas

**1. El recuento de ensayos es una cota inferior.** Las configuraciones
probadas antes de existir el registro no se pueden recuperar. Todas las
correcciones son por tanto optimistas. Declarado en cada informe.

**2. La PBO no llega a 0,5 con ruido puro.** Medido: 40 series de ruido dan
una PBO en torno a 0,24. La causa es que ambas mitades salen de la misma
muestra finita, así que una serie con media global afortunada lo es en las dos.
Por eso la PBO no se usa aislada y el SPA va primero.

**3. La prueba tiene falsos negativos.** Medido (CA-43): con tres series de
Sharpe verdadero 2,2 entre cincuenta candidatos y ocho años de datos, sólo una
se valida. El criterio es deliberadamente asimétrico: el coste de un falso
positivo es capital perdido; el de un falso negativo, una oportunidad no
aprovechada.

## Consecuencia práctica esperada

Con 207 combinaciones, diez años de datos diarios y un coste de financiación de
dos dígitos en el lado comprador, lo más probable es que ninguna combinación
sobreviva. Eso es el sistema funcionando, no fallando.
