# ADR-0007 — El contraste se hace contra comprar y mantener

Fecha: 2026-08-20
Estado: aceptado
Corrige: ADR-0005

## Contexto

ADR-0005 definió el contraste SPA contra un benchmark de cero, es decir, contra
"no operar". La justificación era que un sistema que puede elegir no estar en el
mercado tiene ese listón como referencia natural.

Era un defecto de diseño.

## El problema

Contrastar contra cero pregunta **"¿gana dinero?"**. En un mercado que subió
durante diez años, cualquier estrategia sesgada a comprar responde que sí sin
mérito propio: está capturando la subida, no encontrando ineficiencias.

Dos síntomas lo confirmaron en producción:

**1. El p-valor salía saturado.** 0,0010 en las cuatro ejecuciones realizadas
sobre datos reales, que es el mínimo posible con 1.000 réplicas de bootstrap.
Un estadístico que siempre satura no está midiendo.

**2. El top 10 eran todo índices y metales comprados.** Ni un solo par de
divisas. Coherente con capturar beta, no con encontrar alfa.

## Demostración

Cincuenta estrategias construidas SIN ninguna habilidad, que sólo capturan
entre el 40% y el 90% de un mercado alcista:

| Benchmark | p-valor | Conclusión que emite |
|---|---|---|
| No operar (cero) | 0,0020 | "Hay evidencia estadística" |
| Comprar y mantener | 0,8862 | "No hay nada" |

El benchmark anterior declaraba evidencia donde no la había. Verificado en
CA-93.

## Decisión

Cada combinación se contrasta contra **comprar y mantener su propio
instrumento**, alineado a las mismas fechas.

La pregunta pasa de "¿gana dinero?" a **"¿gana más que quedarse quieto?"**, que
es la única que justifica el coste, el riesgo y el esfuerzo de operar.

El benchmark anterior sigue disponible en el panel como opción, y el informe
declara siempre cuál se usó: un p-valor de 0,001 contra cero y otro contra el
mercado parecen el mismo resultado y significan cosas muy distintas.

## Consecuencias

**Positiva.** El contraste mide capacidad predictiva en lugar de exposición al
mercado.

**Negativa.** Es considerablemente más duro. Es previsible que ninguna
combinación lo supere. Eso no es un fallo: superar a comprar y mantener después
de costes es difícil, y la mayoría de los gestores profesionales no lo consigue.

**Verificado que no rechaza todo.** CA-94 comprueba que una estrategia con
exceso genuino sobre el mercado sigue detectándose.
