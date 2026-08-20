# ADR-0003 — GBI provee exclusivamente CFDs; consecuencias sobre el roadmap de estrategias

- **Estado**: Aceptado
- **Fecha**: 2026-08-05
- **Supersede a**: ninguno
- **Relacionado con**: ADR-0001 (unión discriminada de instrumentos)
- **Cierra el riesgo**: R1 del Módulo 01 (con resultado desfavorable)
- **Afecta a**: catálogo de instrumentos, Módulo 06 (Backtesting), Módulo 07
  (Strategy Engine), Módulo 09 (Risk Engine)

---

## 1. Contexto

El diseño del Módulo 01 identificó como riesgo de mayor impacto (R1) la
posibilidad de que los símbolos ofrecidos por GBI en MetaTrader 5 fueran CFDs
en lugar de futuros de bolsa. Tres de las quince estrategias previstas —Term
Structure, Carry y la componente de bonos de Macro Allocation— dependen de
acceder a la curva real de futuros.

La auditoría se realizó el 2026-08-05 sobre el terminal MT5 conectado a GBI,
inspeccionando el árbol de símbolos y la especificación de contrato.

## 2. Evidencia

### 2.1 Estructura del árbol de símbolos

```
MetaTrader 5
├── Forex
├── Commodities
├── Indices
│   ├── Indices Spot
│   │   ├── Major Spot Indices
│   │   └── Minor Spot Indices
│   └── Indices Futures
│       ├── VIX_Z4      (vencido: dic-2024)
│       └── VIX_H5      (vencido: mar-2025)
├── Crypto
├── Bonds CFDs          ← el bróker los etiqueta CFD
├── Stock CFD's         ← el bróker los etiqueta CFD
└── Custom
```

La rama **Indices Futures contiene únicamente dos símbolos, ambos vencidos**.
No existen futuros de índices negociables (ES, NQ, YM, GER40, etc.).

### 2.2 Contratos "con vencimiento" son CFDs declarados

Con `Show expired contracts` activado, los únicos contratos datados son:

| Símbolo | Descripción según GBI |
|---|---|
| `DXY_U6` | US Dollar Index - September 26 **CFD** |
| `VIX_Q6_CFD` | Volatility Index - August 26 **CFD** |

El propio bróker incluye "CFD" en la descripción. La columna `Expiration`
permanece vacía incluso con contratos expirados visibles: **no hay histórico de
contratos vencidos, por tanto no hay curva reconstruible**.

### 2.3 Especificación de contrato (US30, representativo de Major Spot Indices)

| Campo | Valor | Lectura |
|---|---|---|
| **Calculation** | **CFD** | MT5 declara el modo de cálculo. Concluyente |
| Digits | 2 | `tick_size = 0.01` |
| Contract size | 1 | `contract_multiplier = 1` |
| Spread | floating | Coste estocástico, no constante |
| Margin / Profit currency | USD | `currency = "USD"` |
| Tick size / Tick value | 0.00 / 0 | Sin poblar; derivar desde Contract size y Digits |
| Execution | Market | Ejecución del bróker, no de bolsa |
| Swap type | USD | Cargo en dólares por lote y noche |
| **Swap long** | **−12.503** | |
| **Swap short** | **−0.572** | |
| Swap rates | Lun–Jue: 1, **Vie: 3** | Triple el viernes, cubre fin de semana |
| Margin rate | 0.0050 | Apalancamiento 200:1 |
| Sessions | Cotiza 00:00–24:00, opera 01:00–23:59 | Una hora de cierre diario |

## 3. Decisión

### 3.1 Modelado

Todos los instrumentos de GBI se catalogan como `CFD` (ADR-0001), con
`issuer="GBI"` y `financing_applies=True`. Ninguno se cataloga como `Future`.

`Future` permanece en el modelo de dominio: se usará cuando se incorpore una
fuente de datos de futuros reales, y el hecho de que la clase exista sin
instancias es la señal explícita de esa carencia.

### 3.2 Estrategias bloqueadas

| Estrategia | Estado | Motivo |
|---|---|---|
| **Term Structure** | **BLOQUEADA** | No existe curva. Sin contratos de distintos vencimientos, la estrategia no tiene input |
| **Carry** | **BLOQUEADA** | Lo medible es el swap que fija GBI, no el carry económico del activo. Una estrategia construida sobre esto modelaría la política de precios del bróker |
| **Macro Allocation** | **PARCIAL** | Viable con índices y divisas. La componente de futuros de bonos queda fuera; "Bonds CFDs" no son el futuro del Treasury |

**Condición de desbloqueo**: incorporación de un proveedor de datos de futuros
reales (Databento, Barchart o CME directo) con histórico de contratos vencidos
suficiente para reconstruir la curva. Ver §5.

### 3.3 Estrategias no afectadas

Trend Following, Cross Sectional Momentum, Mean Reversion, Volatility Breakout,
Pairs Trading, Statistical Arbitrage, Factor Investing, Seasonality, Risk-Off
Allocation, Volatility Targeting, Session Effects y el ML Meta Model operan sin
objeción sobre CFDs. **Once de quince estrategias siguen en pie.**

### 3.4 Modelo de coste de financiación (requisito del Módulo 06)

El Backtesting Engine **debe** aplicar swap a toda posición mantenida overnight.
Un backtest sin este coste sobrestima el retorno de forma sistemática y creciente
con el horizonte de tenencia.

```
coste_swap_diario = swap_por_lote × lotes × multiplicador_del_dia
multiplicador_del_dia = 3 si viernes, 1 en el resto
```

Para US30, largo, 1 lote:

| Periodo | Coste |
|---|---|
| Una noche (lun–jue) | 12,50 USD |
| Una noche (viernes) | 37,51 USD |
| Semana completa | 87,52 USD |
| Anualizado sobre ~44.000 USD de exposición | **≈ 10,3%** |

**Asimetría estructural**: el corto cuesta 22× menos que el largo (0,57 vs
12,50). Cualquier optimización que no modele el swap sobreponderará posiciones
largas por una razón puramente artificial. Este sesgo debe estar activo desde
el primer backtest, no añadirse después.

### 3.5 Consecuencia sobre el Risk Engine (Módulo 09)

Con margen 0.005, un lote de ~44.000 USD de exposición requiere ~220 USD de
margen. **El dimensionamiento de posición no puede basarse en margen
disponible**: el bróker permitiría abrir exposición desproporcionada respecto
al capital. El sizing debe calcularse sobre volatilidad realizada y exposición
nocional, tratando el margen como una restricción no vinculante.

### 3.6 Consecuencia sobre el Data Quality Engine (Módulo 02)

La ventana de cotización (00:00–24:00) excede la de negociación (01:00–23:59).
La hora de cierre diario producirá huecos sistemáticos en series intradía que
**no son datos faltantes** sino cierre programado. El Módulo 02 debe
distinguirlos usando el calendario de sesiones del instrumento antes de aplicar
cualquier detección de huecos.

## 4. Consecuencias

**Positivas**

- El riesgo de mayor impacto del proyecto queda cerrado con evidencia
  documental antes de escribir una sola estrategia.
- La separación `CFD` / `Future` de ADR-0001 impide estructuralmente que una
  estrategia de carry acepte estos instrumentos. La decisión, que parecía
  excesiva en su momento, resulta ser exactamente el caso real.
- El coste de financiación queda cuantificado antes del primer backtest, en
  lugar de descubrirse al comparar backtest contra cuenta real.

**Negativas**

- Se pierden dos estrategias del plan original y parte de una tercera.
- Todos los precios provienen del feed de GBI, no del mercado subyacente.
  `is_broker_specific=True` en `ProviderCapabilities` ya lo refleja.
- El sistema queda expuesto a cambios unilaterales de GBI en swaps y spreads.
  **Mitigación**: registrar la especificación de contrato periódicamente y
  alertar ante cambios, ya que alteran la rentabilidad de estrategias en
  producción sin que nada falle visiblemente.

## 5. Alternativa diferida: separar datos de ejecución

**Camino B**, evaluado y pospuesto deliberadamente.

Consiste en obtener datos de futuros reales de un proveedor externo
(Databento, Barchart) para investigación, manteniendo GBI únicamente como
plataforma de ejecución manual sobre el CFD correlacionado. Requiere un módulo
de mapeo instrumento-investigación → instrumento-ejecución y el modelado del
tracking error entre ambos.

**Por qué se pospone**: implica una suscripción de datos de coste recurrente
significativo. No es razonable comprometerla antes de disponer de performance
real de las once estrategias viables. La decisión se reevalúa al cerrar el
Módulo 14 (Performance Analytics), cuando haya evidencia sobre la que decidir.

## 6. Revisión

Este ADR se revisa si GBI incorpora futuros reales a su oferta, o al
incorporarse un proveedor de datos de futuros. Cualquiera de los dos supuestos
desbloquea Term Structure y Carry y requiere un ADR que supersede a este.
