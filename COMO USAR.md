# QQ Quant OS — Guía de uso

Guía práctica. Todos los comandos se escriben en **PowerShell**, dentro de la
carpeta del proyecto.

---

## Preparación (una sola vez)

Abre PowerShell y sitúate en el proyecto:

```
cd C:\dev\qq-quant-os
```

Activa el entorno e instala lo necesario para el panel visual:

```
.venv\Scripts\Activate.ps1
pip install -e ".[dev,dashboard,data,informe]"
```

---

## Paso 1 — Comprobar que todo funciona

```
pytest -q
```

Debe aparecer **345 passed**. Si sale eso, el sistema está sano.

---

## Paso 2 — Descargar datos reales de mercado

```
python scripts\descargar_datos.py
```

Tarda unos dos minutos. Descarga 10 años de histórico diario de los 23
instrumentos del catálogo desde Stooq (gratuito, sin registro).

Verás una tabla con el resultado de cada instrumento. Los datos vienen de Yahoo Finance. Si algún instrumento aparece como
"SIN DATOS", el script te dice cuál: se corrige el identificador en el
catálogo. Existe también un proveedor alternativo:

```
python scripts\descargar_datos.py --fuente stooq
```

Los datos quedan en un fichero llamado `qq_data.db`, en la misma carpeta.

Opciones:

```
python scripts\descargar_datos.py --anios 20
python scripts\descargar_datos.py --simbolos US500 EURUSD XAUUSD
```

Puedes ejecutarlo las veces que quieras: no duplica datos.

---

## Paso 3 — Generar señales y backtests

```
python scripts\generar_senales.py
```

Calcula, para cada instrumento, qué recomienda cada estrategia hoy, y ejecuta
un backtest histórico con los costes reales del bróker.

Verás dos tablas: las señales vigentes y los resultados históricos. Se guardan
además en `senales.csv` y `backtests.csv`, que se pueden abrir con Excel.

---

## Paso 4 — Ver las órdenes del día

```
python scripts\ordenes_del_dia.py
```

Es la salida final del sistema: qué comprar o vender hoy, cuántas unidades,
a qué precio y con qué nivel de invalidación. Incluye la justificación de cada
orden.

Se puede ajustar el capital y el riesgo:

```
python scripts\ordenes_del_dia.py --capital 250000 --riesgo 0.5
```

El sistema **no ejecuta** estas órdenes. Es el operador quien decide.

---

## Paso 5 — Generar el informe para compartir

```
python scripts\generar_informe.py
```

Crea `informe_qq_quant_os.html` en la carpeta del proyecto: un archivo único
con los gráficos y las tablas dentro.

Se puede **adjuntar a un correo**. Quien lo reciba lo abre con doble clic y se
ve en cualquier navegador, sin instalar nada. Es la forma de compartir
resultados con alguien que no tiene el sistema en su máquina.

---

## Paso 6 — Abrir el panel visual

```
streamlit run dashboard\app.py
```

Se abre solo en el navegador. Si no, ve a `http://localhost:8501`.

El panel tiene cuatro secciones:

| Pestaña | Qué muestra |
|---|---|
| **Cartera** | Cómo habría evolucionado una cuenta siguiendo las señales: capital, posiciones abiertas y resultado |
| **Señales** | Qué recomienda cada estrategia hoy sobre cada instrumento, con su justificación |
| **Backtesting** | Resultado histórico de cada estrategia con costes reales |
| **Precios** | Gráfico histórico, rentabilidad, volatilidad y caída máxima |
| **Catálogo** | Los 23 instrumentos con su símbolo de ejecución y de investigación |
| **Estado de los datos** | Cobertura de cada serie y qué falta por descargar |
| **Costes de financiación** | Cuánto cuesta mantener cada posición abierta |

Para cerrarlo: `Ctrl+C` en PowerShell.

---

## Guardar los cambios en Git

Cada vez que haya avances:

```
git add .
git commit -m "Descripción del avance"
```

---

## Si algo falla

| Síntoma | Solución |
|---|---|
| `no se reconoce python` | Cierra y reabre PowerShell |
| `no module named qq_core` | Falta activar el entorno: `.venv\Scripts\Activate.ps1` |
| `no module named streamlit` | Ejecuta `pip install -e ".[dashboard]"` |
| El panel dice "no hay base de datos" | Ejecuta primero el paso 2 |
| Muchos instrumentos sin datos | Normal en la primera ejecución. Anota cuáles y se corrigen en el catálogo |


---

## Registrar operaciones reales

La pestaña **Mi cuenta** del panel es donde se anota lo que realmente se
ejecuta en MetaTrader.

Al registrar una operación se puede indicar si vino de una señal del sistema o
fue una decisión propia. Esa distinción permite medir tres cosas:

- **Deslizamiento**: cuánto peor fue el precio real frente al recomendado.
- **Retraso de decisión**: días entre que la señal se generó y se ejecutó.
- **Disciplina**: si se respetó el nivel de invalidación fijado al abrir.

Ninguna de las tres puede medirse en un sistema automático, porque en él no
existe la brecha entre recomendación y ejecución. Es la ventaja concreta de
operar manualmente.

**Distinción importante:** la pestaña «Mi cuenta» contiene dinero real. La
pestaña «Histórico simulado» contiene una reconstrucción de lo que habría
pasado. No deben presentarse como equivalentes.


---

## Novedades de la versión 1.3

**Reparto de capital.** Pestaña nueva que dice cuánto asignar a cada señal
según su convicción, el comportamiento histórico de la estrategia, la
volatilidad del instrumento y su correlación con lo ya asignado. Incluye un
tope de riesgo agregado para toda la cartera.

**Trailing stop.** El nivel de invalidación acompaña al precio cuando la
posición avanza, sin retroceder nunca. Deja correr las ganancias en lugar de
devolverlas.

**Informe de por qué gana o pierde.** Descompone el resultado: cuánto aporta
la lógica de la estrategia, cuánto se lleva la financiación, si funciona
comprando o vendiendo, y si el resultado depende de pocas operaciones.

**Seguimiento de señales.** Cada posición abierta se reevalúa a diario: si la
señal que la justificó se mantiene, se ha reforzado, se debilita o se ha
invertido.

**Filtros por convicción.** En señales y en el reparto de capital se puede
exigir sólo señales fuertes.

**Simulación por estrategia.** El histórico se puede ejecutar con cualquier
combinación de estrategias.

**Corrección del oro y la plata.** Estaban mapeados al futuro del COMEX en
lugar de al contado, lo que producía medias móviles distintas de las del
terminal. Ya usan el precio al contado.

**Verificación de indicadores.** En cada mercado se pueden consultar los
valores exactos que usa el sistema, para contrastarlos con MetaTrader.


---

## Novedades de la versión 1.4

**Seguimiento de señales.** Pestaña nueva. Cuando tomes una operación, márcala
desde «Señales de hoy» y el sistema la reevaluará cada día: si la proyección
se mantiene, se refuerza, se debilita o se ha invertido. Ordena poniendo
primero lo que requiere revisión.

No hace falta registrar precio ni unidades: marcar una señal cuesta un clic.
Para llevar la contabilidad completa está «Mi cuenta».

**Filtro por convicción en el histórico simulado.** Ahora se puede simular qué
habría pasado operando sólo las señales fuertes, o sólo las moderadas o
superiores. Para ello cada estrategia calcula su convicción a lo largo de todo
el histórico, no sólo en el último día.


---

## Novedades de la versión 1.5 — Control de calidad de datos

Se ha construido el **Módulo 02**, que verifica la suposición sobre la que se
apoya todo lo demás: que los datos son correctos. Era la deuda más antigua del
proyecto.

### Qué comprueba

Precios cero o negativos, barras incoherentes, fechas duplicadas, series
congeladas, huecos, valores atípicos, saltos de nivel, series desactualizadas
e histórico insuficiente.

### Qué hace con lo que encuentra

Puntúa cada serie de 0 a 100 y **excluye del análisis las que no superan el
control**. No basta con avisar de que una serie está mal: hay que dejar de
generar señales sobre ella.

### Desde la línea de comandos

```
python scripts\revisar_calidad.py
python scripts\revisar_calidad.py --detalle
```

Devuelve error si alguna serie resulta inutilizable, así que la actualización
automática diaria lo detecta.

### Dos decisiones que conviene conocer

**No corrige nada.** Detecta, cuantifica y decide si la serie sirve. Un dato
corregido en automático es indistinguible de uno correcto, y eso destruye la
trazabilidad.

**Distingue «falta descargar» de «esta serie está rota».** Si todo el universo
está igual de atrasado, es una descarga pendiente y sólo se avisa. Si una serie
concreta se quedó atrás mientras las demás se actualizaban, esa serie sí se
descarta.


---

## Novedades de la versión 1.6

### Nueva fuente de datos con intradía

Se ha añadido **Twelve Data**, que aporta algo que Yahoo no da: velas de 5
minutos, 15 minutos y 1 hora. Con ella se pueden analizar temporalidades
cortas sin conectar el terminal del bróker.

Requiere una clave gratuita de twelvedata.com. Una vez obtenida, en PowerShell:

```
$env:TWELVEDATA_API_KEY = "tu_clave_aqui"
```

Para que quede guardada permanentemente:

```
[Environment]::SetEnvironmentVariable("TWELVEDATA_API_KEY", "tu_clave", "User")
```

Y para descargar datos intradía:

```
python scripts\descargar_datos.py --fuente twelvedata --timeframe 1h
python scripts\descargar_datos.py --fuente twelvedata --timeframe 5m
```

**Sobre TradingView:** no publica una API de datos. Las librerías que circulan
raspan su web, violan sus condiciones de uso y se rompen cada vez que cambian
el sitio. No es una base aceptable para un sistema de empresa.

### Marcar señales con un clic

En «Señales de hoy» ahora hay una casilla junto a cada señal. Se marcan las
que se hayan operado y se pulsa «Guardar seguimiento». Ya no hay que buscar la
señal en un desplegable aparte.

Cada fila muestra además el **capital sugerido** para esa operación: el
importe, el porcentaje del capital, las unidades y cuánto se arriesga.

### Tamaño recomendado en el seguimiento

Cada posición en seguimiento indica el tamaño que le correspondería **hoy**,
recalculado con la señal vigente. Si la convicción bajó o la volatilidad
cambió, el tamaño recomendado cambia con ella.

### Explicación del reparto de capital

La pestaña de reparto incluye una guía que aclara la confusión más habitual:
la diferencia entre el **riesgo** (lo que se pierde si salta el stop) y el
**capital comprometido** (el dinero que ocupa la posición). Un capital
comprometido alto no significa arriesgar mucho: significa que el stop está
cerca.


---

## Versión 1.7 — Tus datos ya no se pierden

### El problema

Las señales marcadas para seguimiento desaparecían al día siguiente.

La causa: **Streamlit Community Cloud borra el disco cada vez que la aplicación
se reinicia, se despliega o despierta tras dormirse.** Los precios se vuelven a
descargar solos, pero el seguimiento y las operaciones registradas son datos
del operador y sólo existían allí.

Fue un error de arquitectura: se guardaron datos irreemplazables en el mismo
almacenamiento temporal que los regenerables.

### La solución, en tres niveles

**1. Copia automática.** Cada vez que marcas una señal o registras una
operación, el sistema guarda una copia. Al arrancar, si detecta que la base
está vacía pero hay copia, la restaura sola. No tienes que hacer nada.

Conserva cinco generaciones, así que un borrado accidental puede deshacerse.

**2. Descarga manual.** En «Sistema» hay un botón para descargar tus datos y
otro para volver a subirlos. Restaurar el mismo archivo dos veces no duplica
nada, así que puedes hacerlo sin miedo.

**3. Base de datos externa.** Es la solución definitiva. Configurando la
variable `QQ_USER_DATA_URL` con una dirección de PostgreSQL —hay servicios
gratuitos como Supabase o Neon— los datos dejan de depender del servidor.

### Qué hacer mientras tanto

El panel te indica en qué nivel estás. Si dice **temporal**, descarga tus datos
al terminar cada sesión de trabajo.

La copia automática cubre los reinicios cortos, pero un despliegue nuevo
también borra el directorio temporal. Sólo la base de datos externa lo resuelve
por completo.


---

## Versión 1.9 — Validación estadística y motor de portafolio

### Lo que cambia de fondo

Hasta ahora el panel avisaba en rojo de que los resultados no estaban
corregidos por selección múltiple. Avisar no lo arregla. Ahora está corregido.

**El dato que lo justifica:** 207 series de números aleatorios, sin ninguna
capacidad de predecir nada, producen un Sharpe anualizado de **0,71** en la
mejor de ellas. Sin corregir, eso parece una estrategia decente. Es sólo el
premio de haber mirado 207 veces.

### Nueva pestaña «Validación»

Ejecuta tres pruebas y hay que superar las tres:

- **Contraste conjunto (SPA de Hansen)** con bootstrap por bloques
- **Deflated Sharpe Ratio**, que descuenta el número de candidatos evaluados
- **Probabilidad de sobreajuste (PBO)**: ¿elegir por backtest generaliza?

### El Signal Quality Score no puede saltarse la validación

Una combinación que no supera la corrección recibe 0 y clasificación
REJECTED, por muy bueno que sea su backtest. No es una advertencia que se pueda
ignorar: está cerrado con llave en el código y verificado por pruebas.

### Motor de portafolio

Perfil de cuenta con tres perfiles de riesgo y todos los límites editables.
Dimensionamiento por riesgo con multiplicadores acotados. Estimación de riesgo
para estrategias sin stop fijo. Rechazo automático de señales cuyo objetivo no
cubre el coste de financiación.

### Preparado para el equipo de tecnología

El puerto `AccountProvider` define el contrato del gateway MT5: equity, margen,
posiciones reales y swaps medidos. La estructura y las pruebas están escritas;
falta el servicio HTTP. Contrato en `docs/gateway/CONTRATO-CUENTA.md`.

**Regla:** lo que no se puede medir NO se rellena con un cero. El panel dice
"no disponible" en lugar de mostrar un margen falso del 0%.

### Descarga intradía

`scripts/descargar_intradia.py` respeta el límite de 8 peticiones por minuto de
Twelve Data. Sirve para ver el mercado y contrastar Yahoo, **no** para
backtestear intradía: el plan gratuito guarda semanas, no años.

### Dos correcciones de fondo

- **Sharpe clásico** junto al geométrico. Las correcciones estadísticas están
  derivadas sobre el clásico; pasarles el otro da un número sin error visible
  y equivocado.
- **Tipo sin riesgo explícito.** Estaba implícitamente en cero. Un backtest de
  prueba pasa de Sharpe 0,30 a 0,04 al usar un 4%.

### Lo que sigue pendiente

1. Medir el swap de los 22 instrumentos restantes — es entrada de todo lo demás
2. Fijar el tipo sin riesgo real y volver a validar
3. Gateway MT5
4. Fases 9 a 14 del roadmap del motor de portafolio


---

## Versión 1.9.1 — Corrección

Una prueba (CA-90) fallaba en equipos que SÍ tenían configurada la clave de
Twelve Data, y pasaba en los que no. El adaptador trataba `api_key=""` igual
que `api_key=None`, así que caía a la variable de entorno en ambos casos.

Una prueba cuyo resultado depende de cómo esté configurado el ordenador no es
una prueba. Corregido en dos sitios: el adaptador ahora distingue "no me pasas
clave" de "me pasas una clave vacía a propósito", y la prueba se aísla del
entorno. Añadida CA-92 para que no vuelva a ocurrir.


---

## Versión 1.10 — Costes de financiación reales

Se midieron los swaps en el terminal con `scripts/medir_swaps.py`. **17 de 23
instrumentos pasan de estimación a medición real.**

### El error que se corrige

La estimación del -9% anual era razonable para índices y metales, y muy
equivocada para divisas y petróleo:

| Grupo | Coste real comprado | Error del -9% |
|---|---|---|
| Divisas | -2,7% a +2,7% | 6 a 12 puntos |
| Petróleo | **+4,9% y +19,1%** | 14 a 28 puntos |
| Metales | -5,2% y -8,4% | aceptable |
| Índices euro | -15,2% | 6 puntos |

El petróleo comprado **cobra** en lugar de costar: es carry de backwardation.

### Y un error mayor: las divisas no pagaban nada

`FxSpot` no tenía campo de financiación. Todos los backtests de divisas se
ejecutaron **sin coste alguno**. Corregido, con dos pruebas nuevas para que no
vuelva a pasar.

### Los swaps caducan

US500 medía -13,36% el 6 de agosto y -8,63% el 19. Siguen a los tipos de
interés. Hay que volver a ejecutar `medir_swaps.py` periódicamente.

### ADR-0003 corregido

Sí hay futuros (`US500.U26`, `USTEC.U26`) y el bróker ofrece 1.144 símbolos,
incluidos acciones y cripto. Carry y Term Structure siguen bloqueadas, pero
por falta de vencimientos simultáneos, no por ausencia de futuros. Ver ADR-0006.

### Qué hay que hacer ahora

**Rehacer los 207 backtests y volver a validar.** El informe anterior se
calculó con costes incorrectos.


---

## Versión 1.11 — Benchmark correcto

### El defecto que se corrige

Hasta ahora la validación contrastaba cada estrategia contra **no operar**. Esa
pregunta es "¿gana dinero?". En un mercado que subió durante diez años,
cualquier estrategia sesgada a comprar responde que sí sin ningún mérito
propio: captura la subida, no encuentra ineficiencias.

Ahora se contrasta contra **comprar y mantener el propio instrumento**. La
pregunta pasa a ser "¿gana MÁS que quedarse quieto?", que es la única que
justifica el coste y el riesgo de operar.

### La demostración

Cincuenta estrategias SIN ninguna habilidad, que sólo capturan parte de un
mercado alcista:

| Benchmark | p-valor |
|---|---|
| No operar | **0,0020** — "hay evidencia" |
| Comprar y mantener | **0,8862** — "no hay nada" |

El benchmark anterior declaraba evidencia donde no la había. Verificado en
CA-93.

### `volatility_breakout`: diagnosticada, no parcheada

Pierde en los seis pares de divisas a la vez. El diagnóstico descarta error de
signo o de lógica: sobre un paseo aleatorio sin costes da Sharpe +0,035, o sea
neutra.

Los hallazgos reales son otros dos:

1. **Declara horizonte de 1 semana y dura 1,5 días.** Su regla de salida
   documentada (objetivo, invalidación, retorno al rango) NO está implementada:
   recalcula la posición cada barra, sin estado.
2. **El coste no la explica.** Sólo está expuesta el 10% del tiempo.

Pierde porque compra rupturas que se desvanecen. **No se ha tocado**: ajustarla
para que salga positiva sería sobreajustar, y contaría como ensayos nuevos que
suben el listón para todo lo demás.

### Actualizar sin perder datos

Ver `ACTUALIZAR.md`. A partir de esta versión se actualiza con `git pull` en
lugar de borrar la carpeta. Conserva la base de datos, el entorno y el
historial.


---

## Versión 1.11 — Benchmark correcto

### El defecto que se corrige

Hasta ahora el contraste estadístico comparaba cada estrategia contra **no
operar**. Eso pregunta "¿gana dinero?". En un mercado que subió diez años,
cualquier estrategia compradora responde que sí sin mérito propio: está
capturando la subida, no encontrando ineficiencias.

Ahora compara contra **comprar y mantener el propio instrumento**, que pregunta
"¿gana MÁS que quedarse quieto?".

### La demostración

Cincuenta estrategias SIN ninguna habilidad, que sólo capturan parte de un
mercado alcista:

| Benchmark | p-valor | Qué concluía |
|---|---|---|
| No operar | 0,0020 | "Hay evidencia" |
| Comprar y mantener | 0,8862 | "No hay nada" |

El benchmark anterior declaraba evidencia donde no la hay.

**Aviso:** el listón nuevo es mucho más duro. Es previsible que no lo supere
nada. Superar a comprar y mantener después de costes es difícil, y la mayoría
de gestores profesionales no lo consigue.

En el panel puedes elegir el benchmark y el informe declara siempre cuál se usó.

### Diagnóstico de `volatility_breakout`

Se investigó por qué pierde en los seis pares de divisas a la vez. Sobre 40
paseos aleatorios sin costes da Sharpe +0,035: **no hay error de lógica ni de
signo**. Pierde porque compra rupturas que se desvanecen.

Sí se encontró un defecto: declara horizonte de 1 semana y promete tres reglas
de salida que no están implementadas. Dura 1,5 días de media. Ver
`docs/modules/05-validacion/DIAGNOSTICO-volatility-breakout.md`.

No se ha tocado la estrategia: ajustarla hasta que dé positivo sería
sobreajustar, y cada intento sube el listón para todas las demás.

### Actualizar sin perder datos

Nuevo `ACTUALIZAR.md`. A partir de esta versión se actualiza con `git pull` en
lugar de borrar la carpeta, conservando la base de datos, el entorno virtual y
el historial. **Esta es la última vez que hace falta reinstalar desde cero.**


---

## Versión 1.12 — Diversificación por riesgo

### Validación retirada

El módulo de validación estadística sale del flujo y del panel, por decisión de
negocio. El código sigue en el repositorio y puede reconectarse. Ver ADR-0008.

Se conservan dos salvaguardas que no son validación sino higiene básica: el
mínimo de operaciones para puntuar y el encogimiento por tamaño de muestra. Sin
ellas, una estrategia con 8 operaciones afortunadas recibiría más capital que
una con 300 y buen historial.

### Reparto por riesgo, no por capital

Nueva sección en «Reparto de capital». Responde a tres preguntas:

**¿Cuál es la mejor y cuánto le pongo?** La prioridad combina calidad histórica
(60%), persistencia de la señal (15%) y aportación a la diversificación (25%).
Ese último factor hace que **una señal con peor puntuación pueda adelantar a
otra mejor** si la segunda duplica exposición que ya tienes.

**¿De dónde saco capital mañana?** Se reserva un porcentaje configurable del
presupuesto de riesgo. Por defecto el 40%.

**¿Cómo diversifica de verdad?** Con el concepto de apuestas independientes
efectivas: cinco posiciones con correlación 0,9 equivalen a 1,2 apuestas
reales. El motor reparte sobre esa cifra.

Ejemplo medido con cuatro índices y dos activos independientes: US500 recibe
0,50% de riesgo; USTEC, con calidad casi igual, recibe 0,17% por correlación;
DJ30 se rechaza por correlación de 0,95; y EURUSD, con la peor calidad,
adelanta a DE40 y DJ30 porque diversifica.

### Equity sin bróker

El estado de la cuenta se calcula desde tu capital declarado y las operaciones
en seguimiento: equity = capital + resultado cerrado + resultado flotante. El
gateway MT5 pasa a ser opcional.

El riesgo abierto se calcula por distancia al stop, no por nocional. Una
posición de 10.000 con el stop al 2% arriesga 200, no 10.000.

### Histórico simulado filtrable por instrumento

Nuevo selector: todos los instrumentos, o los que elijas. Por ejemplo sólo
US500 y F40.

### Stooq intradía

Confirmado que Stooq ofrece datos horarios y de 5 minutos para 66 pares de
divisas y 56 índices. **Falta comprobar cuánto histórico da:** descarga un
archivo de `stooq.com/db/h/` y mira la primera fecha del CSV. Si son años, el
backtest intradía se desbloquea sin necesidad del gateway MT5.


---

## Versión 1.13 — Análisis del seguimiento y asistente

### El reparto ahora trabaja sobre TUS operaciones

Antes analizaba las señales del día. Ahora toma las que **tú has marcado en
seguimiento** y responde a la pregunta real:

> "Tengo seis operaciones en seguimiento. ¿Cuál tiene más probabilidad de
> llegar a su objetivo? ¿Cuánto capital a cada una? ¿O mejor espero?"

**Probabilidad de alcanzar el objetivo.** Combina dos cosas:

- **Geometría** — si el objetivo está al doble de distancia que el stop,
  alcanzarlo antes es menos probable por pura aritmética. Se mide desde el
  precio de HOY, así que una operación que ya avanzó puntúa más alto.
- **Histórico** — el porcentaje de veces que esa combinación llegó a su
  objetivo, con peso según cuánta muestra haya.

**Valor esperado.** Una probabilidad baja puede compensar si el premio es
grande. Se muestra en múltiplos del riesgo.

**Y puede decir que esperes.** Si una operación no aporta —correlación alta con
otra que ya tienes, presupuesto agotado, grupo saturado— aparece en «Las que
conviene esperar, y por qué», con su explicación.

Cada cifra viene con su nivel de confianza. Es una estimación para ordenar
operaciones entre sí, no una predicción.

### Asistente

Cerebro flotante abajo a la izquierda, con el chat en la barra lateral.

Puedes preguntarle qué señales fuertes hay hoy, cómo van tus operaciones, si
ha cambiado algo desde ayer, o cuánto arriesgar y dónde.

**Sólo responde con datos reales del sistema.** El estado se construye en
código y se le entrega cerrado: no tiene forma de consultar nada por su cuenta
ni de rellenar huecos. Si algo no está en el panel, lo dice en lugar de
inventarlo.

Requiere clave de API. Ver `CONFIGURAR ASISTENTE.md`. **Aviso de coste:** cada
pregunta se factura, y el panel público permite que cualquiera con el enlace
gaste tu saldo. Pon un límite mensual en la consola de Anthropic.


---

## Versión 1.14 — Asistente gratuito y gráfico de señales

### Asistente, sin pagar nada

Cerebro flotante abajo a la izquierda, chat en la barra lateral. Funciona en
dos niveles:

**Gratuito.** Reconoce unas 30 clases de pregunta y responde al instante
leyendo los datos reales. Sin coste. Escribe **ayuda** para ver todo lo que
sabe. Cubre señales del día, tu seguimiento, reparto de capital, estado de
cuenta, riesgo abierto, correlaciones, catálogo, costes de financiación y un
glosario de conceptos.

**De pago, opcional.** Si preguntas algo que el modo gratuito no reconoce y
tienes clave de API configurada, consulta al modelo. Sin clave, te muestra lo
que sí sabe hacer en lugar de improvisar.

**No puede inventar cifras.** El modo gratuito rellena plantillas con datos de
la base de datos; no genera texto libre. Y una pregunta fuera del sistema
—"¿va a subir el bitcoin?"— no recibe respuesta inventada.

### Gráfico de cada señal

En «Señales de hoy», elige una señal y verás:

- El precio en velas de las últimas sesiones
- **Los indicadores concretos que usó esa estrategia**: las dos medias del
  seguimiento de tendencia, las bandas y el z-score de la reversión, el techo y
  suelo del rango en la ruptura de volatilidad
- Los tres niveles marcados: entrada, invalidación y objetivo
- Las zonas de riesgo y beneficio sombreadas
- La justificación de la señal y la relación beneficio/riesgo

Si una estrategia no expone indicadores dibujables, se muestra sólo el precio y
se declara. **No se dibujan indicadores genéricos**: harían creer que la señal
viene de ahí.
