# Módulo 01 — Data Engine

| Campo | Valor |
|---|---|
| Versión | 1.0.0 |
| Estado | Implementado — pendiente de validación con datos reales de GBI |
| Paquete | `qq_core` v0.1.0 |
| `transform_version` | 1.0.0 |
| Depende de | Ninguno |
| Bloquea a | 02 Data Quality, 03 Feature Engine, y todo lo posterior |

---

## 1. Objetivo

Convertir datos heterogéneos de proveedores externos en **series canónicas,
versionadas y trazables**, almacenadas de forma idempotente.

### Alcance

- Modelo de dominio: `Instrument`, `Bar`, `Provenance`.
- Puerto `DataProvider` y adaptadores para MT5/GBI, Stooq, Alpha Vantage, FRED.
- Esquema TimescaleDB con UPSERT idempotente y registro de revisiones.
- Servicio de ingesta troceado, reanudable y con reintentos.
- Catálogo de instrumentos y mapeo símbolo-proveedor → símbolo canónico.

### Fuera de alcance (deliberadamente)

| Excluido | Módulo responsable | Por qué |
|---|---|---|
| Detección de huecos y outliers | 02 Data Quality | Un Data Engine que "arregla" datos hace imposible saber qué entregó el proveedor |
| Empalme de futuros continuos | 02 Data Quality | Requiere calendario de bolsa y política de roll |
| Desplazamiento point-in-time de series macro | 02 Data Quality | Requiere calendario de publicaciones |
| Cualquier cálculo de indicadores | 03 Feature Engine | Separación de responsabilidades |
| Envío de órdenes | Ninguno. Nunca | Requisito del proyecto, garantizado por CA-11 |

---

## 2. Arquitectura

### Diagrama de dependencias

```
                         ┌──────────────────────────┐
                         │   qq_core.domain         │
                         │   Instrument, Bar,       │  ← sin dependencias
                         │   Provenance, enums      │     externas salvo pydantic
                         └────────────┬─────────────┘
                                      │
                 ┌────────────────────┼────────────────────┐
                 │                    │                    │
        ┌────────▼────────┐  ┌────────▼────────┐  ┌───────▼────────┐
        │ ports           │  │ ports           │  │  ingestion     │
        │ DataProvider    │  │ BarRepository   │  │  Service       │
        └────────┬────────┘  └────────┬────────┘  └───────┬────────┘
                 │                    │                   │
     ┌───────────┼─────────┐          │            depende SOLO
     │           │         │          │            de los puertos
┌────▼───┐ ┌─────▼──┐ ┌────▼───┐ ┌────▼─────────┐
│ Stooq  │ │  FRED  │ │  MT5   │ │  Timescale   │
│adapter │ │adapter │ │gateway │ │  repository  │
└────────┘ └────────┘ └───┬────┘ └──────────────┘
                          │ HTTP localhost
                   ┌──────▼────────────────────┐
                   │  mt5-gateway (proceso      │
                   │  Windows, ÚNICO que        │
                   │  importa MetaTrader5)      │
                   └────────────────────────────┘
```

La regla de dependencia es unidireccional: **el dominio no conoce a nadie; los
adaptadores conocen al dominio; nadie conoce a los adaptadores salvo el
ensamblado final**. Cambiar de Stooq a Polygon es escribir una clase nueva y
cambiar una línea en la composición.

### Topología de despliegue

```
HOST WINDOWS (tu máquina actual)
├── Terminal MT5 (GBI)  ← sesión iniciada, solo lectura
├── mt5-gateway.exe     ← proceso nativo, 127.0.0.1:8765
└── Docker Desktop / WSL2
    ├── qq-ingestion    ← contenedor Linux
    ├── qq-api          ← contenedor Linux (FastAPI, Módulo 11+)
    └── timescaledb     ← contenedor Linux
```

El gateway es el único componente no portable. Son ~300 líneas. Todo lo demás
corre igual en tu Windows, en el CI y en un servidor futuro.

### Flujo de una ingesta

```
IngestionRequest
   ↓
_validate            → falla rápido si el rango o el timeframe son inválidos
   ↓
_effective_start     → aplica watermark si resume=True
   ↓
_chunks              → trocea según max_bars_per_request
   ↓
   ├─ fetch_bars     → reintentos con backoff ante ProviderError
   │                  → SIN reintento ante ProviderContractError
   ↓
   └─ upsert_bars    → compara content_hash
                        ├─ ausente  → inserted
                        ├─ igual    → unchanged (no-op, no toca updated_at)
                        └─ distinto → revised + fila en bar_revision
   ↓
IngestionResult      → conteos + chunks fallidos
```

---

## 3. Justificación técnica

### 3.1 Unión discriminada en lugar de `Instrument` genérico

Un `Instrument` único con `expiry: date | None`, `contract_multiplier: Decimal
| None`, `isin: str | None` es el diseño obvio y es el equivocado. Convierte
todo error de modelado en un `None` silencioso que viaja hasta el cálculo de
PnL. Con la unión, construir un `Future` sin vencimiento falla en validación
(CA-01).

El coste es real: el código consumidor debe hacer `match` sobre `asset_class`.
Es deliberado. Obliga a decidir explícitamente qué hace una estrategia escrita
para futuros cuando recibe un bono, en lugar de producir un número sin
sentido.

### 3.2 `CFD` como clase separada de `Future`

Esta es la decisión con más impacto económico del módulo. Un CFD sobre el
S&P 500 emitido por GBI no es el futuro ES del CME: su financiamiento, su
vencimiento y su spread los fija el bróker. Backtestear carry o term structure
sobre CFDs mide la política de precios del bróker, no la curva del mercado.

Al separarlos en el sistema de tipos, una estrategia de term structure
sencillamente no puede aceptar un CFD por accidente (CA-02).

### 3.3 `source` en la clave natural

La misma barra de EURUSD según GBI y según Stooq son **datos distintos** y
deben coexistir (CA-17). La alternativa —elegir un proveedor "verdadero" en la
ingesta— destruye la información necesaria para detectar que el feed del bróker
se desvía del mercado. La reconciliación es responsabilidad del Módulo 02, que
puede verla porque el Módulo 01 no la borró.

### 3.4 `Decimal` en lugar de `float`

Un tick de 0.01 no es representable exactamente en binario. En una serie de
1M de barras con PnL apalancado, el error se acumula. El coste de rendimiento
existe pero es irrelevante: la conversión a `float64` ocurre una vez, en el
Módulo 03, al construir los arrays de features.

### 3.5 `content_hash` e idempotencia

Es el mecanismo que hace reproducible todo lo demás (CA-12). Y su efecto
secundario es más valioso que el primario: al comparar hashes se detecta
**cuándo un proveedor reescribe el histórico** (CA-13). Sin esto, un backtest
deja de reproducir seis meses después y nadie puede explicar por qué.

Se espera que MT5/GBI dispare revisiones con frecuencia: el histórico del
terminal depende de lo que haya descargado en cada sesión.

### 3.6 `ProviderCapabilities` explícito

Un adaptador que no puede cumplir el contrato no se fuerza: declara su
limitación. `has_point_in_time=False` y `is_broker_specific=True` en MT5 son
avisos que el Módulo 02 lee programáticamente. Ocultar una limitación detrás de
una interfaz uniforme es exactamente cómo se cuelan datos incorrectos.

### 3.7 `tick_volume` de MT5 se descarta

MT5 no publica volumen negociado en FX ni en CFDs: publica número de ticks.
Almacenarlo en la columna `volume` produciría features de volumen sin
significado económico que parecerían funcionar en backtest. Se guarda `None`
(CA-09).

---

## 4. Riesgos

| # | Riesgo | Prob. | Impacto | Mitigación |
|---|---|---|---|---|
| R1 | **Los símbolos de GBI son CFDs, no futuros reales.** Invalidaría carry, term structure y macro allocation tal como están planteadas | Alta | Crítico | Auditar la especificación de símbolos de GBI ANTES de poblar el catálogo. Ver §8, tarea de validación |
| R2 | El histórico de MT5 cambia entre sesiones del terminal | Alta | Alto | `bar_revision` + alerta si `revised > 0` |
| R3 | Alpha Vantage a 5 req/min hace inviable cualquier ingesta que no sea diaria batch | Cierta | Medio | Declarado en `capabilities`; usarlo solo para EOD |
| R4 | Stooq ajusta corporate actions de forma inconsistente fuera de US/PL | Media | Alto | `adjusted_for_corporate_actions` explícito en `Equity`; validación cruzada en Módulo 02 |
| R5 | El gateway MT5 es un punto único de fallo en Windows | Media | Medio | Chunks fallidos se reportan sin abortar; reanudación por watermark |
| R6 | Deriva entre la implementación en memoria y la de TimescaleDB | Media | Alto | Suite de contrato compartida que ambas deben pasar |
| R7 | Sobrecarga de `Decimal` en series de millones de barras | Baja | Bajo | Medir antes de optimizar; la conversión a float ocurre en el Módulo 03 |
| R8 | El equipo modifica `Bar` o `Instrument` sin ADR y rompe el histórico | Media | Crítico | `qq-core` versionado con SemVer; cambio a contratos requiere ADR + revisión |

---

## 5. Alternativas consideradas y descartadas

| Alternativa | Por qué se descartó |
|---|---|
| **Importar `MetaTrader5` en el núcleo** | Ata todo —incluido el CI— a un host Windows con MT5 abierto. El gateway cuesta ~300 líneas y libera el resto de la plataforma |
| **Parquet + DuckDB en lugar de TimescaleDB** | Sería mejor para el *research* puro (más rápido, sin servidor). Se descarta porque el sistema también sirve un dashboard con actualización continua, y Parquet no soporta bien escrituras incrementales concurrentes. **Recomendación**: añadir una capa Parquet de solo lectura para backtesting en el Módulo 06, alimentada desde Timescale |
| **Un `Instrument` plano con campos opcionales** | Ver §3.1 |
| **Elegir un proveedor "canónico" en la ingesta** | Destruye la capacidad de detectar la desviación del feed del bróker |
| **`float` para precios** | Ver §3.4 |
| **Limpiar datos en el Data Engine** | Hace imposible auditar qué entregó realmente el proveedor |
| **Ids sintéticos como PK de `bar`** | Permiten insertar el mismo dato dos veces con ids distintos, rompiendo la idempotencia |
| **Política de retención en Timescale** | En una plataforma de investigación, borrar histórico es destruir el activo principal de la empresa |
| **`ts` como timestamp de cierre** | Más seguro contra lookahead, pero obligaría a traducir en cada adaptador. Se protege en el Módulo 03 |

---

## 6. Código

```
src/qq_core/
├── domain/
│   ├── enums.py         AssetClass, Timeframe, DataSource, RollPolicy
│   ├── instrument.py    Unión discriminada: Future, CFD, FxSpot, Equity, ETF, MacroSeries
│   ├── bar.py           Barra canónica con invariantes estructurales
│   └── provenance.py    Trazabilidad y hashing de contenido
├── ports/
│   └── data_provider.py DataProvider, ProviderCapabilities, jerarquía de errores
├── adapters/
│   ├── stooq.py         Adaptador CSV, con fetcher inyectable
│   └── mt5_gateway.py   Cliente HTTP del gateway. NO importa MetaTrader5
├── storage/
│   ├── repository.py    BarRepository + implementación en memoria
│   └── schema.sql       TimescaleDB: hypertable, upsert idempotente, bar_revision
└── ingestion/
    └── service.py       Orquestación: troceado, reanudación, reintentos
```

**Pendiente de esta versión** (planificado, no implementado): adaptadores FRED
y Alpha Vantage, repositorio TimescaleDB, servidor `mt5-gateway`, y CLI de
ingesta. Los tres primeros son mecánicos una vez fijados los contratos; el
gateway requiere tu máquina Windows para probarse.

---

## 7. Pruebas

**43 tests, todos en verde.** Cada uno mapea a un criterio de aceptación o a un
caso límite.

### Criterios de aceptación

| ID | Criterio | Test |
|---|---|---|
| CA-01 | Un futuro sin vencimiento no se puede construir | `test_future_requires_expiry` |
| CA-02 | Un CFD no es tipable como futuro | `test_cfd_is_not_a_future` |
| CA-03 | La unión deserializa a la subclase correcta | `test_instrument_union_dispatches_on_asset_class` |
| CA-04 | No entra ningún timestamp sin zona horaria | `test_bar_rejects_naive_timestamp` |
| CA-05 | Una barra con OHLC incoherente no llega a la BD | `test_bar_rejects_incoherent_ohlc` |
| CA-06 | Una barra desalineada de la rejilla se rechaza | `test_bar_rejects_misaligned_timestamp` |
| CA-07 | El hash de contenido es determinista | `test_content_hash_is_deterministic` |
| CA-08 | Una respuesta con estructura distinta no se parsea a ciegas | `test_stooq_rejects_unexpected_header` |
| CA-09 | El tick volume de MT5 no se almacena como volumen | `test_mt5_discards_tick_volume` |
| CA-10 | El núcleo nunca importa `MetaTrader5` | `test_core_never_imports_metatrader5` |
| CA-11 | No existe código capaz de enviar órdenes | (guardia textual sobre el árbol de `qq_core`) |
| **CA-12** | **Reingestar el mismo rango deja el almacén idéntico** | `test_reingestion_is_byte_identical` |
| CA-13 | Una revisión del proveedor se detecta y se registra | `test_provider_revision_is_detected_and_logged` |
| CA-14 | El rango se trocea sin solapes ni huecos | `test_range_is_chunked_by_provider_limit` |
| CA-15 | La ingesta se reanuda desde el watermark | `test_resume_starts_from_watermark` |
| CA-16 | Un error de contrato no se reintenta | `test_contract_error_is_not_retried` |
| CA-17 | Dos proveedores coexisten para el mismo símbolo | `test_two_sources_coexist_for_same_symbol` |

### Casos límite cubiertos

- Respuesta vacía del proveedor → cero barras, sin excepción.
- `volume=None` vs `volume=0` → hashes distintos; ausencia ≠ cero.
- Timestamp en zona no-UTC → normalizado a UTC, no rechazado.
- Fallo parcial → los chunks restantes se ingestan igualmente y el fallo se reporta.
- Error transitorio → 3 reintentos con backoff exponencial.
- Timeframe no soportado → falla antes de tocar la red.
- Campo mal escrito en un instrumento → `extra="forbid"` lo rechaza.
- Watermark al día → no se hace ninguna petición.

### Cobertura pendiente

- Suite de contrato ejecutada contra TimescaleDB real (requiere Docker).
- Test de integración contra el terminal MT5 real (requiere tu máquina).
- Property-based testing de las invariantes de `Bar` con Hypothesis.

---

## 8. Criterios de salida del Módulo 01

No se pasa al Módulo 02 hasta que:

1. ☐ **Auditoría de símbolos de GBI**: confirmar si son futuros o CFDs. *Este
   es el bloqueante de mayor riesgo (R1).*
2. ☐ `mt5-gateway` implementado y probado contra el terminal real.
3. ☐ Repositorio TimescaleDB implementado, pasando la misma suite de contrato
   que la implementación en memoria.
4. ☐ Adaptadores FRED y Alpha Vantage implementados.
5. ☐ Catálogo poblado con al menos 20 instrumentos reales.
6. ☐ 90 días de histórico ingestado y reingestado, con CA-12 verificado sobre
   datos reales.
7. ☐ Cobertura de tests > 90% en `qq_core`.
8. ☐ `mypy --strict` sin errores.

---

## 9. Registro de cambios

| Versión | Fecha | Cambio |
|---|---|---|
| 1.0.0 | 2026-08-04 | Diseño inicial. Contratos `Instrument`, `Bar`, `Provenance` congelados. Adaptadores Stooq y MT5-gateway. Servicio de ingesta. 43 tests |
