# ADR-0008 — Retirada del módulo de validación del flujo principal

Fecha: 2026-08-20
Estado: aceptado
Afecta a: ADR-0005, ADR-0007

## Contexto

El Módulo 05 aplicaba corrección estadística por selección múltiple y actuaba
como barrera obligatoria del Signal Quality Score: una combinación que no la
superaba recibía cero puntos y no llegaba al reparto de capital.

Sobre datos reales, ninguna de las 198 combinaciones superó la corrección al
contrastar contra comprar y mantener. Con la barrera activa, el sistema no
asignaba capital a nada y resultaba inutilizable en la práctica.

## Decisión

Por decisión de negocio, el módulo `validation` se retira del flujo principal y
de la interfaz. `score_signal` ya no lo requiere.

El código, las pruebas y la documentación permanecen en el repositorio. Volver
a conectarlo es un cambio de una línea en `signal_scoring.py`.

## Lo que se conserva y por qué

Dos salvaguardas que NO son validación estadística sino higiene elemental, y
sin las cuales el reparto de capital daría resultados absurdos:

**Mínimo de operaciones por casilla.** Una combinación con 8 operaciones no se
puntúa. Con retroceso de nivel cuando la casilla específica no tiene muestra.

**Encogimiento por tamaño de muestra.** Una casilla con 35 operaciones se
mezcla con la media global. Sin esto, una combinación con pocas operaciones
afortunadas recibiría más capital que otra con trescientas y buen historial.

## Consecuencia conocida

Las estadísticas históricas que alimentan la puntuación proceden de una
búsqueda entre 198 candidatos. Parte de lo que distingue a las mejores es
mérito y parte es el azar de haber mirado muchas veces. **La puntuación actual
no separa una cosa de la otra.**

Medición que sigue siendo válida: 207 series de números aleatorios, sin
capacidad de predecir nada, producen un Sharpe anualizado de 0,71 en la mejor
de ellas.

Esto se documenta para que quede constancia, no para revertir la decisión.

## Alternativa que se propuso y no se adoptó

Convertir la validación de veto binario en **un factor más de la puntuación**:
una combinación con soporte estadístico débil no quedaría excluida, recibiría
menos peso y menos capital.

Habría permitido que el sistema funcionara —hay señales, hay ranking, hay
reparto— conservando la información sobre el soporte estadístico de cada
combinación. Se descartó en favor de la retirada completa.
