# Diagnóstico de `volatility_breakout`

Fecha: 2026-08-20

## Motivo

En el backtest sobre datos reales, `volatility_breakout` aparece en 8 de las 20
peores combinaciones, con Sharpe entre -0,36 y -0,64 y caídas de hasta el 22%.
Pierde en **los seis pares de divisas a la vez**, y también en DJ30, AUS200,
SWI20 y UK100.

Que una estrategia pierda en un instrumento es azar. Que pierda en todos los
pares simultáneamente no lo es. Se investigó si había un error de lógica.

## Prueba realizada

Se ejecutó la estrategia sobre 40 paseos aleatorios independientes, **sin
costes**. Sobre datos sin ninguna estructura explotable, una estrategia sin
defectos debe dar Sharpe medio cercano a cero.

| Estrategia | Sharpe medio | Desv. típica | Negativo en |
|---|---|---|---|
| `volatility_breakout` | **+0,035** | 0,340 | 23 de 40 |
| `trend_following` | +0,024 | 0,367 | 19 de 40 |

**No hay sesgo estructural.** No es un error de signo ni de lógica: el
`shift(1)` está correctamente aplicado y la estrategia es neutra sobre ruido.

## Lo que sí se encontró

### 1. La documentación describe una estrategia distinta de la implementada

La clase declara `horizon = Horizon.WEEK_1` y su `exit_rule` promete cerrar al
alcanzar el objetivo, al tocar la invalidación, o si el precio vuelve dentro del
rango previo.

Lo implementado en `target_position` **no tiene ninguna de esas tres salidas**:
recalcula la posición en cada barra, sin estado. Medido sobre 10 años
simulados:

- Exposición: 10,0% del tiempo
- Cambios de posición: 332
- **Duración media: 1,5 días**, no una semana

Es una discrepancia real entre lo documentado y lo que corre.

### 2. El coste de financiación no explica las pérdidas

Al estar expuesta sólo el 10% del tiempo, soporta entre -0,27% y -1,53% anual
según el instrumento. Insuficiente para justificar Sharpes de -0,4 a -0,64.

## Conclusión

La estrategia pierde porque **compra rupturas que se desvanecen**: entra en el
extremo del rango y sale al día siguiente. Es un comportamiento coherente con
mercados que revierten a corto plazo, no un fallo de implementación.

## Por qué NO se ha "arreglado"

Modificar los parámetros hasta que dé positivo sería sobreajustar — exactamente
lo que el Módulo 05 existe para detectar. Y cada configuración probada cuenta
como un ensayo adicional, lo que **sube el listón de la validación para todas
las demás combinaciones**.

Se documenta el hallazgo. Si se decide rediseñar la estrategia, debe hacerse
por una razón basada en la lógica del mercado, no en el resultado del backtest,
y el rediseño debe registrarse como ensayo nuevo.

La discrepancia entre `exit_rule` documentada e implementada sí es un defecto
que corregir: o se implementan las salidas prometidas, o se corrige la
documentación para que describa lo que realmente hace.
