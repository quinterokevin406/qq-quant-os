# Revisión técnica del Motor de Portafolio y Riesgo

Respuesta al punto 39 de la especificación. Se entrega antes de dar por cerrado
el diseño, como pedía ese mismo punto.

---

## 1. Cómo entiendo el objetivo

El sistema debe responder cada día a: *dado mi capital, mis posiciones, mis
señales y el estado del mercado, ¿cuánto riesgo está estadísticamente
justificado asignar a cada oportunidad?*

Y debe poder responder **"a ninguna"** aunque haya capital libre. El punto 40 de
la especificación lo dice bien y es la parte más importante del encargo.

---

## 2. Qué ya existía y se reutiliza

No se ha reconstruido nada de esto.

| Componente | Fichero | Uso |
|---|---|---|
| Dimensionamiento por riesgo | `portfolio/risk.py` | Base de `sizing.py` |
| Penalización por correlación | `portfolio/allocation.py` | Se conserva |
| Reparto de capital | `portfolio/allocation.py` | Se conserva |
| Registro de ejecución real | `execution/trade.py` | Ya cubre el punto 15 completo: recomendado vs ejecutado, slippage, retraso de decisión, si se respetó el stop |
| Estados de señal | `signals/watchlist.py` | Base del punto 14 |
| Métricas de cartera | `backtest/engine.py` | Ampliado con Sharpe clásico y tipo sin riesgo |
| Control de calidad de datos | `quality/` | Cubre parte del punto 27 |
| Aislamiento del bróker | ADR-0002, `adapters/mt5_gateway.py` | Patrón reutilizado en ADR-0004 |

El punto 15 de la especificación estaba, en la práctica, ya implementado.

---

## 3. Módulos nuevos en esta entrega

| Módulo | Qué cubre de la especificación |
|---|---|
| `validation/` | Puntos 27 y 28 completos |
| `portfolio/account.py` | Punto 3 |
| `portfolio/signal_scoring.py` | Puntos 4 y 5 |
| `portfolio/sizing.py` | Puntos 6, 7, 8 y 26 |
| `ports/account_provider.py` | Puntos 2 y 12, estructura |
| `adapters/account_providers.py` | Idem, implementaciones |

---

## 4. La decisión de arquitectura más importante

**El Signal Quality Score no puede calcularse sin el informe de validación.**
Es un argumento obligatorio de `score_signal`, no una comprobación opcional.

### Por qué

La especificación pedía puntuar cada señal según su expectativa histórica,
profit factor y Sharpe. Todas esas cifras salen de los mismos 207 backtests sin
corregir.

Un motor que puntúe alto lo que tuvo mejor backtest y luego asigne más capital
a las puntuaciones altas **no es un sistema de gestión de riesgo: es una
máquina que busca los resultados más contaminados por sesgo de selección y les
da más dinero.** Sería más peligroso que no tener el motor, porque el sesgo
vendría envuelto en un número de 0 a 100 con aspecto de rigor.

Al exigir el informe como argumento, ese fallo es **imposible por
construcción**. Verificado por CA-57: dos señales con estadísticas de
operaciones idénticas reciben 85 y 0 según hayan superado o no la validación.

---

## 5. Crítica al modelo de scoring propuesto

### 5.1 El problema de tamaño de muestra

El punto 5 pedía puntuar la combinación estrategia × activo × dirección ×
horizonte × régimen. Son del orden de **1.650 casillas**.

Con unos pocos miles de operaciones en diez años, la mayoría tendría cifras de
un dígito. Una expectativa calculada sobre ocho operaciones es ruido con
decimales. **Esto no se arregla programando mejor: es un límite de la muestra.**

Solución adoptada, en dos partes:

**Jerarquía con retroceso.** Se intenta la casilla específica; si no llega al
mínimo de operaciones, se sube de nivel. El nivel usado queda registrado.

```
estrategia + activo + dirección     (ideal)
estrategia + activo                 (retroceso 1)
estrategia + familia de activos     (retroceso 2)
estrategia                          (retroceso 3)
ninguno                             -> no se puntúa
```

**Encogimiento hacia la media.** La estadística de la casilla se mezcla con la
media global con peso `n / (n + 50)`. Con poca muestra la puntuación tiende a
la media global, que es la respuesta honesta cuando no se sabe nada específico.

### 5.2 Un fallo que las pruebas destaparon

La primera versión encogía también la caída máxima. **Es incorrecto:** la caída
no es la media de nada, es el máximo de una trayectoria. Mezclar una caída
propia del 21% con una media global del 97% produce un número que no describe a
ninguno de los dos, y arrastraba la puntuación a cero.

Corregido: sólo se encogen estadísticos por operación. Documentado en el módulo.

### 5.3 Lo que se acepta de la especificación sin cambios

El uso de la expectativa en lugar del porcentaje de aciertos. Es correcto y es
la métrica que más capital ha destruido en este oficio cuando se usa sola.
Verificado por CA-62.

---

## 6. Crítica a los porcentajes de riesgo propuestos

| Parámetro | Propuesto | Adoptado | Motivo |
|---|---|---|---|
| Riesgo base por operación | 1% | **0,50%** | El 1% clásico asume operaciones independientes. Hay 13 índices que se mueven casi juntos: cinco posiciones al 1% en índices distintos son aproximadamente un riesgo del 4% con etiquetas diferentes |
| Riesgo total abierto | 5% | 5% | Se acepta |
| Riesgo por cluster | 2% | **1,5%** | Con 13 índices, el cluster de renta variable puede absorber la cartera entera. Un 2% ahí, con correlaciones que en crisis tienden a uno, es el presupuesto completo en una sola apuesta direccional |
| Margen máximo | 40% | **25%** | Motivo específico de este bróker: el swap comprador es del −13,36% anual. El margen no es sólo riesgo de garantía, es **coste corriente**. Un 40% en largos a varios meses es una sangría antes de que el mercado haga nada |
| Reducción por caída | desde 5% | **desde 3%** | La propia especificación se contradice: su escala detallada ya empieza en el 3%. Se adopta la escala, que es la coherente |
| Bloqueo por caída | 10% | **8%** | Coherencia con lo anterior |
| Reserva de liquidez | 25% | 25% (30% conservador) | Se acepta |

Todos son configurables. `RiskLimits.validated()` rechaza combinaciones
incoherentes: una configuración contradictoria produce comportamiento
impredecible en lugar de un error visible.

---

## 7. Lo que NO se puede construir hoy, y por qué

### 7.1 Bloqueado por falta de gateway MT5

Equity real, margen utilizado, margen disponible, P&L flotante, apalancamiento
efectivo, libro de posiciones del bróker.

Afecta a los puntos **2, 3 (parcial), 12 y 24** de la especificación.

**Lo entregado:** el puerto `AccountProvider` con dos implementaciones. El
motor funciona hoy sobre valores introducidos a mano; cuando exista el gateway,
se conecta sin tocar el motor. Contrato completo en `docs/gateway/`.

### 7.2 Fuera del alcance de este bróker

El punto 7 pedía soporte para acciones, ETFs, futuros y cripto. **ADR-0003
documenta que GBE Global sólo ofrece CFDs.** Ese código sería para un bróker
que no se tiene.

### 7.3 Corrección a la propia especificación

El documento dice 15 estrategias. **Son 9.** Carry y Term Structure están
bloqueadas por falta de curva de futuros utilizable; Macro Allocation es
parcial. ADR-0003.

### 7.4 Fases pendientes por volumen, no por orden

Puntos 9 a 14 del roadmap: VaR de cartera completo, optimización, backtest del
propio motor de portafolio, paper portfolio, dashboard completo y auditoría
humano contra modelo. Son semanas de trabajo cada una.

---

## 8. Riesgos técnicos

| Riesgo | Mitigación |
|---|---|
| Yahoo limita por volumen (`Too Many Requests` observado en producción) | Twelve Data como segunda fuente; gateway MT5 pendiente |
| `use_container_width` quedará obsoleto en Streamlit | Deuda técnica anotada; romperá el panel cuando lo retiren |
| SQLite y multihilo en Streamlit | Ya resuelto con cerrojo; el registro de ensayos usa el mismo patrón |
| El gateway podría implementarse con capacidad de envío de órdenes | El contrato lo prohíbe explícitamente; CI verifica que el núcleo no puede ejecutar |

---

## 9. Riesgos estadísticos

Estos son más graves que los técnicos y no todos tienen solución.

**1. El recuento de ensayos es una cota inferior.** Los ensayos previos al
registro no se pueden recuperar. Todas las correcciones son **optimistas**.
No tiene arreglo retroactivo; se declara en cada informe.

**2. El coste de financiación de 22 de 23 instrumentos es una estimación.**
Si el valor real difiere del −9% supuesto, cambia el Sharpe de todas las
combinaciones que los tocan, cambia su orden, y cambia cuáles sobreviven a la
corrección. **Este informe habría que rehacerlo.** Es la tarea pendiente con
mejor relación entre esfuerzo y valor.

**3. El tipo sin riesgo estaba implícitamente en cero.** Medición propia: un
backtest pasa de Sharpe 0,30 a 0,04 al usar un 4% en lugar de cero. **Una parte
sustancial del Sharpe del sistema podría ser el tipo sin riesgo disfrazado.**

**4. La validación tiene falsos negativos.** Medido: de tres series con Sharpe
verdadero 2,2 entre cincuenta candidatos, sólo una se valida. El criterio es
asimétrico a propósito.

**5. La PBO no es fiable en solitario.** Medido: 0,24 sobre ruido puro en lugar
del 0,5 teórico, porque ambas mitades comparten la misma suerte muestral.

---

## 10. Qué recomiendo hacer a continuación, por orden

1. **Medir el swap de los 22 instrumentos restantes.** Es entrada de todo lo
   demás y es la tarea más barata de la lista.
2. **Fijar el tipo sin riesgo explícitamente** y volver a ejecutar la validación.
3. **Gateway MT5.** Desbloquea los puntos 2, 3, 12 y 24 enteros, más el backtest
   intradía y la redundancia frente a Yahoo.
4. Fases 9 a 14 del roadmap.

---

## 11. Resultado esperado, dicho por adelantado

Con 207 combinaciones, diez años de datos y un swap de dos dígitos en el lado
comprador, **lo más probable es que ninguna combinación sobreviva a la
validación.**

Eso no es un fracaso del sistema. Es el sistema haciendo su trabajo.

Un informe que diga *"de 207 candidatos, ninguno resiste el ajuste por selección
múltiple; se recomienda no operar"* es infinitamente más defendible que una
tabla con Sharpe de 1,4 sin corregir.
