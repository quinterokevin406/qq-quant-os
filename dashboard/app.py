"""Panel de control de QQ Quant OS.

Uso local:
    streamlit run dashboard/app.py

ORGANIZACIÓN
------------
Las pestañas siguen el flujo de trabajo del operador, no la arquitectura del
sistema:

  1. Señales de hoy — qué operar, agrupado por estrategia.
  2. Estrategias — qué hace cada motor y cómo se ha comportado.
  3. Cartera — cómo habría evolucionado una cuenta siguiendo las señales.
  4. Mercados — precios e histórico.
  5. Catálogo — gestión del universo operable.
  6. Sistema — estado de los datos y costes del bróker.

No contiene lógica de negocio: sólo lee de `qq_core` y muestra. Un panel con
lógica dentro es imposible de probar y de reemplazar.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from assets.logo import (  # noqa: E402
    AZUL, BORDE, CLARO, CSS_TEMA, GRIS, PANEL, ROJO, VERDE,
    header_html, logo_marca, ticker_card,
)

from qq_core.backtest.engine import financing_from_instrument, run_backtest  # noqa: E402
from qq_core.portfolio.account import PERFILES, RiskProfile  # noqa: E402
from qq_core.assistant import (  # noqa: E402
    AYUDA,
    Assistant,
    AssistantError,
    Snapshot,
    SystemContext,
    responder,
)
from qq_core.charts import (  # noqa: E402
    ChartLevels,
    build_signal_chart,
    levels_summary,
)
from qq_core.portfolio.target_probability import (  # noqa: E402
    estimate_target_probability,
)
from qq_core.portfolio.diversification import (  # noqa: E402
    Candidate,
    allocate_by_risk,
    build_clusters,
)
from qq_core.catalog.overlay import (  # noqa: E402
    CatalogOverlay,
    active_catalog,
    full_catalog_status,
)
from qq_core.domain.enums import DataSource, Timeframe  # noqa: E402
from qq_core.domain.instrument import CFD  # noqa: E402
from qq_core.domain.signal import (  # noqa: E402
    Direction,
    Horizon,
    Signal,
    SignalStrength,
)
from qq_core.execution.journal import (  # noqa: E402
    TradeJournal,
    account_summary,
    execution_quality,
    strategy_performance,
)
from qq_core.execution.trade import ExitReason, RealTrade, build_trade_id  # noqa: E402
from qq_core.features.engine import bars_to_frame  # noqa: E402
from qq_core.portfolio.allocation import (  # noqa: E402
    allocate_capital,
    allocation_summary,
    score_strategies,
)
from qq_core.portfolio.risk import RiskConfig  # noqa: E402
from qq_core.portfolio.simulator import simulate_portfolio  # noqa: E402
from qq_core.storage.backup import (  # noqa: E402
    backup_info,
    restore_if_empty,
    save_backup,
)
from qq_core.storage.remote import (  # noqa: E402
    is_configured as remoto_configurado,
    pull_from_remote,
    push_to_remote,
    remote_status,
)
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402
from qq_core.storage.user_data import (  # noqa: E402
    UserDataSnapshot,
    detect_persistence,
    export_user_data,
    import_user_data,
    suggested_filename,
)
from qq_core.catalog.instruments import METALES_USAN_FUTUROS  # noqa: E402
from qq_core.features import engine as fx  # noqa: E402
from qq_core.quality.issues import Severity  # noqa: E402
from qq_core.signals.tracking import SignalHealth, track_signal  # noqa: E402
from qq_core.strategies.library import BLOCKED_STRATEGIES, build_registry  # noqa: E402

DB_PATH = Path(__file__).resolve().parents[1] / "qq_data.db"

st.set_page_config(
    page_title="QQ Quant OS",
    page_icon="◐",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(CSS_TEMA, unsafe_allow_html=True)


# ------------------------------------------------------------------------- #
# Acceso a datos
# ------------------------------------------------------------------------- #


@st.cache_resource
def get_repo() -> SQLiteBarRepository:
    return SQLiteBarRepository(DB_PATH)


@st.cache_resource
def restaurar_al_arrancar() -> dict | None:
    """Recupera los datos del operador si se perdieron en un reinicio.

    Se ejecuta una sola vez por sesión, antes de abrir el seguimiento o el
    diario. Sin esto, un reinicio de la aplicación borraría las señales
    marcadas sin que el operador pudiera hacer nada.
    """
    # 1. Base de datos remota: es la copia autoritativa cuando existe.
    if remoto_configurado():
        try:
            recuperado = pull_from_remote(DB_PATH)
            if recuperado:
                return recuperado
        except Exception:  # noqa: BLE001
            # Si la base remota no responde, se sigue con la copia local en
            # lugar de dejar al operador sin sus datos.
            pass

    # 2. Copia automática local, como respaldo.
    try:
        return restore_if_empty(DB_PATH)
    except Exception:  # noqa: BLE001
        return None


@st.cache_resource
def get_watchlist():
    """Lista de señales bajo seguimiento del operador."""
    from qq_core.signals.watchlist import Watchlist

    restaurar_al_arrancar()
    return Watchlist(DB_PATH)


@st.cache_resource
def get_journal() -> TradeJournal:
    """Diario de operaciones reales. Comparte fichero con los precios."""
    restaurar_al_arrancar()
    return TradeJournal(DB_PATH)


def guardar_copia() -> None:
    """Guarda una copia tras cada cambio del operador.

    Se llama después de marcar o retirar seguimiento y de registrar
    operaciones. Una copia que sólo se hace cuando el operador se acuerda no
    protege de un reinicio, que llega sin avisar.
    """
    try:
        save_backup(DB_PATH)
    except Exception:  # noqa: BLE001
        pass
    if remoto_configurado():
        try:
            push_to_remote(DB_PATH)
        except Exception:  # noqa: BLE001
            # El cambio ya está guardado localmente; que falle el envío al
            # remoto no debe impedir la operación.
            pass


@st.cache_data(ttl=300)
def resumen_mercados(simbolos: tuple[str, ...]) -> list[dict]:
    """Último precio y variación diaria de los mercados indicados.

    Alimenta la fila de cotizaciones de la portada. Se limita a los
    instrumentos pasados para no recalcular las 23 series en cada recarga.
    """
    filas = []
    for simbolo in simbolos:
        frame = load_frame(simbolo, 1)
        if len(frame) < 2:
            continue
        cierre = frame["close"]
        actual = float(cierre.iloc[-1])
        previo = float(cierre.iloc[-2])
        variacion = (actual / previo - 1) * 100 if previo else 0.0
        digitos = 4 if actual < 10 else 2
        filas.append(
            {"simbolo": simbolo, "precio": actual,
             "variacion": variacion, "digitos": digitos}
        )
    return filas


@st.cache_data(ttl=300)
def regimen_de_mercado() -> dict:
    """Régimen actual, medido sobre el índice de referencia.

    Se usa el S&P 500 como termómetro porque es el mercado más líquido y el
    que mejor resume el apetito global por el riesgo. Un régimen no es una
    predicción: describe el estado presente, no lo que vendrá.
    """
    frame = load_frame("US500", 2)
    if len(frame) < 220:
        return {"estado": "Sin datos", "color": GRIS, "icono": "—",
                "volatilidad": None, "detalle": "Histórico insuficiente"}

    from qq_core.features import engine as fx

    cierre = frame["close"]
    media_larga = fx.sma(cierre, 200).iloc[-1]
    media_corta = fx.sma(cierre, 50).iloc[-1]
    vol = float(fx.realized_volatility(cierre, 20).iloc[-1]) * 100
    precio = float(cierre.iloc[-1])

    if precio > media_larga and media_corta > media_larga:
        estado, color, icono = "Alcista", VERDE, "▲"
    elif precio < media_larga and media_corta < media_larga:
        estado, color, icono = "Bajista", ROJO, "▼"
    else:
        estado, color, icono = "En transición", "#d9a441", "◆"

    if vol > 25:
        detalle = f"Volatilidad elevada · {vol:.1f}%"
    elif vol > 15:
        detalle = f"Volatilidad normal · {vol:.1f}%"
    else:
        detalle = f"Volatilidad contenida · {vol:.1f}%"

    return {"estado": estado, "color": color, "icono": icono,
            "volatilidad": vol, "detalle": detalle}


def precios_actuales() -> dict[str, Decimal]:
    """Último cierre de cada instrumento, para valorar posiciones abiertas."""
    precios = {}
    for entry in active_catalog():
        frame = load_frame(entry.symbol, 1)
        if not frame.empty:
            precios[entry.symbol] = Decimal(str(round(float(frame["close"].iloc[-1]), 6)))
    return precios


@st.cache_data(ttl=120)
def active_source() -> DataSource | None:
    return get_repo().primary_source()


@st.cache_data(ttl=120)
def load_inventory() -> pd.DataFrame:
    rows = get_repo().inventory()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["first_ts"] = pd.to_datetime(df["first_ts"], format="mixed", utc=True)
    df["last_ts"] = pd.to_datetime(df["last_ts"], format="mixed", utc=True)
    return df


@st.cache_data(ttl=300)
def load_frame(symbol: str, years: int) -> pd.DataFrame:
    fuente = active_source()
    if fuente is None:
        return bars_to_frame([])
    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * years)
    return bars_to_frame(
        get_repo().get_bars(symbol, Timeframe.D1, fuente, start, end)
    )


@st.cache_resource(ttl=300)
def signal_objects(years: int) -> list:
    """Objetos `Signal` vigentes, para los módulos que los necesitan enteros.

    El panel usa mayormente la versión en tabla, pero la asignación de capital
    y el seguimiento necesitan la señal completa con sus tipos originales.
    """
    registro = build_registry()
    aptas = usable_universe(years)
    salida = []
    for entry in active_catalog():
        if aptas and entry.symbol not in aptas:
            continue
        frame = load_frame(entry.symbol, years)
        if frame.empty:
            continue
        for estrategia in registro.values():
            if len(frame) < estrategia.warmup_bars + 5:
                continue
            s = estrategia.explain(frame, entry.symbol, entry.instrument.name)
            if s is not None:
                salida.append(s)
    return salida


@st.cache_data(ttl=600)
def quality_reports(years: int) -> dict:
    """Diagnóstico de calidad de cada serie del universo.

    Se calcula sobre todo el universo a la vez porque la detección de huecos
    compara unos instrumentos con otros: sin el resto, no se puede distinguir
    un festivo de un dato que falta.
    """
    from qq_core.quality.checks import check_universe

    frames = {}
    for entry in active_catalog():
        frame = load_frame(entry.symbol, years)
        if not frame.empty:
            frames[entry.symbol] = frame
    if not frames:
        return {}
    return check_universe(frames)


@st.cache_data(ttl=600)
def usable_universe(years: int) -> set:
    """Instrumentos aptos para generar señales.

    Las series que no superan el control de calidad se excluyen del análisis.
    Es la consecuencia práctica del Módulo 02: no basta con avisar de que una
    serie está mal, hay que dejar de usarla.
    """
    from qq_core.quality.checks import usable_symbols

    informes = quality_reports(years)
    if not informes:
        return set()
    return usable_symbols(informes)


@st.cache_data(ttl=300)
def compute_signals(years: int) -> pd.DataFrame:
    """Señal vigente de cada estrategia sobre cada instrumento activo."""
    registro = build_registry()
    aptas = usable_universe(years)
    filas = []
    for entry in active_catalog():
        if aptas and entry.symbol not in aptas:
            # Serie descartada por el control de calidad: no se generan
            # señales sobre datos que no son fiables.
            continue
        frame = load_frame(entry.symbol, years)
        if frame.empty:
            continue
        inst = entry.instrument
        fin = inst.financing if isinstance(inst, CFD) else None
        for estrategia in registro.values():
            if len(frame) < estrategia.warmup_bars + 5:
                continue
            s = estrategia.explain(frame, entry.symbol, inst.name)
            if s is None:
                continue
            fila = s.to_row()
            fila["_estrategia_id"] = s.strategy
            fila["_direccion"] = s.direction.value
            fila["_categoria"] = entry.group
            fila["Coste financiación %"] = (
                s.financing_cost_estimate(fin.annual_rate_long if fin else None)
                if s.direction is Direction.LONG
                else s.financing_cost_estimate(fin.annual_rate_short if fin else None)
            )
            filas.append(fila)
    return pd.DataFrame(filas)


@st.cache_data(ttl=600)
def compute_backtests(years: int) -> pd.DataFrame:
    registro = build_registry()
    aptas = usable_universe(years)
    filas = []
    for entry in active_catalog():
        if aptas and entry.symbol not in aptas:
            # Serie descartada por el control de calidad: no se generan
            # señales sobre datos que no son fiables.
            continue
        frame = load_frame(entry.symbol, years)
        if frame.empty:
            continue
        inst = entry.instrument
        fin = inst.financing if isinstance(inst, CFD) else None
        for estrategia in registro.values():
            if len(frame) < estrategia.warmup_bars + 10:
                continue
            try:
                fila = run_backtest(
                    frame, estrategia, entry.symbol, financing=fin
                ).summary_row
                fila["Estrategia"] = estrategia.label
                filas.append(fila)
            except ValueError:
                continue
    return pd.DataFrame(filas)


@st.cache_data(ttl=600)
def simulate(
    years: int, capital: float, riesgo: float, max_pos: int,
    estrategias: tuple[str, ...] = (),
    min_strength: str | None = None,
    simbolos: tuple[str, ...] = (),
):
    """Reconstruye la cartera con el subconjunto indicado.

    Args:
        simbolos: Instrumentos a incluir. Vacío significa todos. Permite ver la
            estadística de un solo instrumento o de un grupo concreto, en lugar
            de la cartera completa.
    """
    registro = build_registry()
    if estrategias:
        registro = {
            k: v for k, v in registro.items() if v.label in estrategias
        }
    if not registro:
        return None
    precios, instrumentos = {}, {}
    for entry in active_catalog():
        if simbolos and entry.symbol not in simbolos:
            continue
        frame = load_frame(entry.symbol, years)
        if len(frame) < 250:
            continue
        precios[entry.symbol] = frame
        instrumentos[entry.symbol] = entry.instrument
    if not precios:
        return None
    return simulate_portfolio(
        precios, instrumentos, registro,
        initial_capital=Decimal(str(capital)),
        risk_config=RiskConfig(
            risk_per_trade_pct=Decimal(str(riesgo)), max_positions=max_pos
        ),
        min_strength=min_strength,
    )


def descargar_datos(anios: int = 10) -> None:
    """Descarga el histórico desde el propio panel."""
    from qq_core.adapters.yahoo import YahooProvider
    from qq_core.ingestion.service import IngestionRequest, IngestionService

    repo = get_repo()
    service = IngestionService(YahooProvider(), repo, max_retries=2)
    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * anios)
    entradas = [e for e in active_catalog() if e.yahoo_symbol]

    barra = st.progress(0.0)
    estado = st.empty()
    ok = 0
    for i, entry in enumerate(entradas):
        estado.write(f"Descargando {entry.symbol} — {entry.instrument.name}...")
        try:
            service.ingest(
                IngestionRequest(
                    provider_symbol=entry.yahoo_symbol,
                    canonical_symbol=entry.symbol,
                    timeframe=Timeframe.D1,
                    start=start, end=end, resume=False,
                )
            )
            ok += 1
        except Exception:  # noqa: BLE001
            pass
        barra.progress((i + 1) / len(entradas))
    estado.write(f"Completado: {ok} de {len(entradas)} instrumentos.")
    st.cache_data.clear()


# ------------------------------------------------------------------------- #
# Cabecera
# ------------------------------------------------------------------------- #

_ultimo = ""
if DB_PATH.exists():
    _inv_previo = load_inventory()
    if not _inv_previo.empty:
        _ultimo = f"Datos al {_inv_previo['last_ts'].max().date().isoformat()}"

st.markdown(
    header_html(
        "SISTEMA DE INVERSIÓN CUANTITATIVA",
        estado="En línea" if _ultimo else "",
        actualizado=(
            f"{_ultimo} · próxima actualización 07:00 NY" if _ultimo else ""
        ),
    ),
    unsafe_allow_html=True,
)
st.caption(
    "El sistema genera recomendaciones documentadas; la decisión y la "
    "ejecución corresponden al operador."
)

inventory = load_inventory() if DB_PATH.exists() else pd.DataFrame()

if inventory.empty:
    st.info(
        "**Primera ejecución.** Hay que descargar el histórico de mercado antes "
        "de poder analizar nada: unos 58.000 datos de precio de 23 instrumentos, "
        "con 10 años de profundidad. Tarda dos o tres minutos."
    )
    if st.button("Descargar datos de mercado", type="primary"):
        descargar_datos()
        st.rerun()
    st.stop()

with st.sidebar:
    st.markdown(
        f'<div style="display:flex;justify-content:center;padding:4px 0 14px 0;">'
        f'{logo_marca(52)}</div>',
        unsafe_allow_html=True,
    )
    st.subheader("Configuración")
    anios = st.slider("Años de histórico a usar", 1, 20, 10)
    st.divider()
    ultima = inventory["last_ts"].max()
    st.caption(f"**Último dato**\n\n{ultima.date().isoformat()}")
    st.caption(f"**Instrumentos activos**\n\n{len(active_catalog())}")
    st.caption(f"**Barras almacenadas**\n\n{get_repo().total_bars():,}")
    st.divider()
    if st.button("Actualizar datos ahora"):
        descargar_datos(anios)
        st.rerun()
    st.caption(
        "En la versión publicada los datos se actualizan solos cada día "
        "laborable a las 7:00, hora de Nueva York."
    )

(tab_inicio, tab_senales, tab_seguimiento, tab_reparto, tab_cuenta,
 tab_estrategias, tab_cartera, tab_mercados, tab_calidad, tab_catalogo,
 tab_sistema) = st.tabs(
    ["Panorama", "Señales de hoy", "Seguimiento", "Reparto de capital",
     "Mi cuenta", "Estrategias", "Histórico simulado", "Mercados",
     "Calidad de datos", "Catálogo", "Sistema"]
)


# ------------------------------------------------------------------------- #
# Calidad de los datos
# ------------------------------------------------------------------------- #

with tab_calidad:
    st.subheader("Control de calidad de los datos")
    st.caption(
        "Un error de datos no produce ningún fallo: produce resultados "
        "creíbles y falsos. Este módulo verifica la suposición sobre la que "
        "se apoya todo lo demás."
    )

    with st.spinner("Analizando la calidad de las series..."):
        informes = quality_reports(anios)

    if not informes:
        st.info("Sin datos que analizar.")
    else:
        aptas_n = sum(1 for r in informes.values() if r.is_usable)
        criticas = [r for r in informes.values() if r.critical_issues]
        media = sum(r.score for r in informes.values()) / len(informes)
        total_problemas = sum(len(r.issues) for r in informes.values())

        q1, q2, q3, q4 = st.columns(4)
        q1.metric("Series analizadas", len(informes))
        q2.metric(
            "Aptas para señales", f"{aptas_n} de {len(informes)}",
            help="Las series que no superan el control se excluyen "
                 "automáticamente del análisis.",
        )
        q3.metric("Puntuación media", f"{media:.1f}")
        q4.metric("Problemas detectados", total_problemas)

        if criticas:
            st.error(
                f"**{len(criticas)} serie(s) no son utilizables:** "
                + ", ".join(sorted(r.symbol for r in criticas))
                + ". El sistema ha dejado de generar señales sobre ellas."
            )
        else:
            st.success(
                "Ninguna serie presenta problemas críticos. Todas se están "
                "usando para generar señales."
            )

        st.markdown("##### Estado por instrumento")
        resumen_q = pd.DataFrame(
            [r.summary_row() for r in informes.values()]
        ).sort_values("Puntuación")
        st.dataframe(resumen_q, use_container_width=True, hide_index=True)

        problemas = [
            problema.to_row()
            for informe in informes.values()
            for problema in informe.issues
        ]
        if problemas:
            st.markdown("##### Problemas detectados")
            tabla_p = pd.DataFrame(problemas)

            filtro_grav = st.multiselect(
                "Gravedad",
                [s.value for s in Severity],
                default=[Severity.CRITICAL.value, Severity.WARNING.value],
            )
            if filtro_grav:
                tabla_p = tabla_p[tabla_p["Gravedad"].isin(filtro_grav)]

            if tabla_p.empty:
                st.info("Ningún problema con la gravedad seleccionada.")
            else:
                st.dataframe(
                    tabla_p.sort_values("Gravedad"),
                    use_container_width=True, hide_index=True,
                )

        with st.expander("Qué comprueba este módulo"):
            st.markdown(
                """
                | Comprobación | Qué detecta |
                |---|---|
                | **Precios no positivos** | Ceros o negativos: siempre error del proveedor |
                | **Barras incoherentes** | Máximo por debajo del mínimo, cierre fuera de rango |
                | **Fechas duplicadas** | La misma sesión repetida; distorsiona las medias |
                | **Serie congelada** | Precio idéntico varios días: el proveedor repitió el último dato |
                | **Huecos** | Sesiones que otros mercados sí registraron |
                | **Valores atípicos** | Saltos incompatibles con la volatilidad del instrumento |
                | **Salto de nivel** | División de acciones sin ajustar o empalme incorrecto |
                | **Sin actualizar** | La serie dejó de recibir datos |
                | **Histórico corto** | No hay barras suficientes para las estrategias |

                **Sobre los huecos.** Sin el calendario oficial de cada bolsa no
                se puede saber con certeza si un día faltaba por festivo. La
                aproximación es comparar con el resto del universo: si la
                mayoría de mercados tienen ese día y uno no, es un fallo de
                datos. Si falta en todos, fue festivo.

                **Sobre los valores atípicos.** El umbral es deliberadamente
                alto —ocho desviaciones típicas—. Los mercados tienen caídas
                del 10% reales, y marcarlas como error borraría precisamente
                los días que más importan para medir el riesgo.

                **Este módulo no corrige nada.** Detecta, cuantifica y decide
                si la serie sirve. Un dato corregido en automático es
                indistinguible de uno correcto, y eso destruye la
                trazabilidad.
                """
            )


# ------------------------------------------------------------------------- #
# Seguimiento de las señales marcadas
# ------------------------------------------------------------------------- #

with tab_seguimiento:
    st.subheader("Señales en seguimiento")
    st.caption(
        "Operaciones que has marcado desde «Señales de hoy». Cada una se "
        "reevalúa con los datos más recientes: si la proyección se mantiene, "
        "se refuerza, se debilita o se ha invertido."
    )

    nivel = detect_persistence(DB_PATH)
    if nivel.warning:
        (st.error if not nivel.is_durable else st.info)(nivel.warning)

    capital_seguimiento = st.number_input(
        "Capital de referencia ($)", 1_000, 100_000_000, 100_000, step=10_000,
        key="capital_seg_tab",
        help="Se usa para recalcular el tamaño recomendado de cada posición "
             "con los datos de hoy.",
    )

    lista = get_watchlist()
    entradas = lista.active_entries()

    if not entradas:
        st.info(
            "**Nada en seguimiento todavía.** Cuando tomes una operación, "
            "márcala en la pestaña «Señales de hoy» y aparecerá aquí con su "
            "evolución diaria."
        )
    else:
        objetos_actuales = {
            (o.symbol, o.strategy): o for o in signal_objects(anios)
        }
        precios_seg = precios_actuales()

        estados = []
        for identificador, original, nota, alta in entradas:
            precio = precios_seg.get(original.symbol)
            if precio is None:
                continue
            actual = objetos_actuales.get((original.symbol, original.strategy))
            estado = track_signal(original, actual, precio)
            estados.append((identificador, estado, nota, alta))

        prioridad = {
            SignalHealth.REVERSED: 0, SignalHealth.CLOSED: 1,
            SignalHealth.EXPIRED: 2, SignalHealth.WEAKENING: 3,
            SignalHealth.HOLDING: 4, SignalHealth.STRENGTHENED: 5,
        }
        estados.sort(key=lambda e: prioridad[e[1].health])

        atencion = [e for e in estados if e[1].needs_attention]
        reforzadas = [
            e for e in estados if e[1].health is SignalHealth.STRENGTHENED
        ]

        v1, v2, v3, v4 = st.columns(4)
        v1.metric("En seguimiento", len(estados))
        v2.metric("Requieren atención", len(atencion))
        v3.metric("Reforzadas", len(reforzadas))
        resultado_medio = (
            sum(e[1].unrealized_pct for e in estados) / len(estados)
            if estados else 0.0
        )
        v4.metric("Resultado medio", f"{resultado_medio:+.2f}%")

        if atencion:
            st.warning(
                f"**{len(atencion)} posición(es) requieren revisión hoy.** "
                f"La señal que las justificaba ha cambiado."
            )

        for identificador, estado, nota, alta in estados:
            color = {
                SignalHealth.REVERSED: ROJO,
                SignalHealth.CLOSED: ROJO,
                SignalHealth.EXPIRED: "#d9a441",
                SignalHealth.WEAKENING: "#d9a441",
                SignalHealth.HOLDING: GRIS,
                SignalHealth.STRENGTHENED: VERDE,
            }[estado.health]

            with st.container():
                st.markdown(
                    f'<div style="border-left:3px solid {color};'
                    f'padding-left:12px;margin:10px 0;">'
                    f'<span style="font-size:15px;font-weight:600;color:{CLARO};'
                    f'font-family:ui-monospace,monospace;">{estado.symbol}</span>'
                    f'<span style="font-size:12px;color:{color};margin-left:10px;">'
                    f'{estado.health.value.upper()}</span>'
                    f'<span style="font-size:12px;color:{GRIS};margin-left:10px;">'
                    f'{estado.strategy} · marcada el {alta.isoformat()}</span>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

                d1, d2, d3, d4 = st.columns(4)
                d1.metric("Resultado", f"{estado.unrealized_pct:+.2f}%")
                d2.metric(
                    "Avance al objetivo",
                    f"{estado.progress_to_target_pct:.0f}%"
                    if estado.progress_to_target_pct is not None else "—",
                )
                d3.metric(
                    "Margen al stop",
                    f"{estado.distance_to_stop_pct:.1f}%"
                    if estado.distance_to_stop_pct is not None else "—",
                )
                d4.metric(
                    "Días", f"{estado.days_elapsed} de {estado.days_expected}"
                )

                st.markdown(estado.message)

                # Tamaño recomendado hoy: se recalcula con la señal vigente,
                # no con la del día en que se marcó, porque la convicción y la
                # volatilidad han podido cambiar.
                señal_hoy = objetos_actuales.get(
                    (estado.symbol, original.strategy)
                )
                if señal_hoy is not None and señal_hoy.is_actionable:
                    sugerido = allocate_capital(
                        [señal_hoy], Decimal(str(capital_seguimiento)),
                        max_positions=1, max_total_risk_pct=100.0,
                    )
                    if sugerido:
                        a = sugerido[0]
                        st.caption(
                            f"**Tamaño recomendado hoy:** "
                            f"${a.capital_amount:,.0f} "
                            f"({a.capital_pct:.1f}% del capital · "
                            f"{a.units:.4f} unidades). "
                            f"Riesgo si toca la invalidación: "
                            f"${float(capital_seguimiento) * a.risk_pct / 100:,.0f} "
                            f"({a.risk_pct:.2f}%)."
                        )

                if nota:
                    st.caption(f"Tu nota: {nota}")

                cols = st.columns([1, 5])
                if cols[0].button("Retirar", key=f"quitar_{identificador}"):
                    lista.remove(identificador)
                    guardar_copia()
                    st.rerun()
                st.divider()


# ------------------------------------------------------------------------- #
# Panorama — lo primero que se ve al entrar
# ------------------------------------------------------------------------- #

with tab_inicio:
    PRINCIPALES = ("US500", "USTEC", "DE40", "XAUUSD", "USOIL", "EURUSD")
    disponibles_hoy = set(inventory["symbol"])
    cotizaciones = resumen_mercados(
        tuple(s for s in PRINCIPALES if s in disponibles_hoy)
    )

    if cotizaciones:
        st.markdown(
            f'<div style="font-size:11.5px;color:{GRIS};letter-spacing:1.2px;'
            f'margin:4px 0 10px 2px;">MERCADOS PRINCIPALES</div>',
            unsafe_allow_html=True,
        )
        columnas = st.columns(len(cotizaciones))
        for col, c in zip(columnas, cotizaciones):
            col.markdown(
                ticker_card(c["simbolo"], c["precio"], c["variacion"], c["digitos"]),
                unsafe_allow_html=True,
            )
        st.write("")

    with st.spinner("Analizando el mercado..."):
        senales_inicio = compute_signals(anios)
        regimen = regimen_de_mercado()

    izq, der = st.columns([1.4, 1])

    with izq:
        st.markdown(
            f'<div style="background:{PANEL};border:1px solid {BORDE};'
            f'border-radius:12px;padding:18px 20px;">'
            f'<div style="font-size:15px;font-weight:600;color:{CLARO};'
            f'margin-bottom:14px;">Señales de hoy</div>',
            unsafe_allow_html=True,
        )
        if senales_inicio.empty:
            st.info("Sin datos suficientes para generar señales.")
        else:
            compras = int((senales_inicio["_direccion"] == "long").sum())
            ventas = int((senales_inicio["_direccion"] == "short").sum())
            fuera = int((senales_inicio["_direccion"] == "flat").sum())

            m1, m2, m3 = st.columns(3)
            m1.markdown(
                f'<div style="font-size:30px;font-weight:600;color:{VERDE};'
                f'font-variant-numeric:tabular-nums;">{compras}</div>'
                f'<div style="font-size:12px;color:{GRIS};">de compra</div>',
                unsafe_allow_html=True,
            )
            m2.markdown(
                f'<div style="font-size:30px;font-weight:600;color:{ROJO};'
                f'font-variant-numeric:tabular-nums;">{ventas}</div>'
                f'<div style="font-size:12px;color:{GRIS};">de venta</div>',
                unsafe_allow_html=True,
            )
            m3.markdown(
                f'<div style="font-size:30px;font-weight:600;color:{GRIS};'
                f'font-variant-numeric:tabular-nums;">{fuera}</div>'
                f'<div style="font-size:12px;color:{GRIS};">sin posición</div>',
                unsafe_allow_html=True,
            )

            operables = senales_inicio[senales_inicio["_direccion"] != "flat"]
            if not operables.empty:
                orden = {"strong": 0, "moderate": 1, "weak": 2}
                destacadas = operables.assign(
                    _o=operables["Convicción"].map(orden).fillna(9)
                ).sort_values(["_o", "Beneficio/Riesgo"], ascending=[True, False]).head(5)

                st.write("")
                filas_html = ""
                for _, f in destacadas.iterrows():
                    compra = f["Señal"] == "COMPRA"
                    color = VERDE if compra else ROJO
                    fondo = "rgba(26,158,117,0.13)" if compra else "rgba(224,82,82,0.13)"
                    filas_html += (
                        f'<div style="display:flex;justify-content:space-between;'
                        f'align-items:center;padding:8px 0;'
                        f'border-top:1px solid {BORDE};">'
                        f'<div style="display:flex;align-items:center;gap:10px;">'
                        f'<span style="font-size:10.5px;font-weight:600;color:{color};'
                        f'background:{fondo};padding:3px 8px;border-radius:4px;">'
                        f'{f["Señal"]}</span>'
                        f'<span style="font-size:13.5px;color:{CLARO};'
                        f'font-family:ui-monospace,monospace;">{f["Instrumento"]}</span>'
                        f'</div>'
                        f'<span style="font-size:12px;color:{GRIS};">'
                        f'{f["Estrategia"]} · {f["Horizonte"]}</span></div>'
                    )
                st.markdown(filas_html, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)

    with der:
        st.markdown(
            f'<div style="background:{PANEL};border:1px solid {BORDE};'
            f'border-radius:12px;padding:18px 20px;margin-bottom:12px;">'
            f'<div style="font-size:12px;color:{GRIS};margin-bottom:8px;">'
            f'RÉGIMEN DE MERCADO</div>'
            f'<div style="font-size:22px;font-weight:600;color:{regimen["color"]};">'
            f'{regimen["icono"]} {regimen["estado"]}</div>'
            f'<div style="font-size:12px;color:{GRIS};margin-top:6px;">'
            f'{regimen["detalle"]}</div></div>',
            unsafe_allow_html=True,
        )

        cobertura = (
            inventory["last_ts"].max() - inventory["first_ts"].min()
        ).days / 365.25
        filas_sistema = [
            ("Mercados seguidos", f"{len(active_catalog())}"),
            ("Estrategias activas", f"{len(build_registry())}"),
            ("Histórico", f"{cobertura:.0f} años"),
            ("Datos de precio", f"{get_repo().total_bars():,}"),
        ]
        html_sistema = (
            f'<div style="background:{PANEL};border:1px solid {BORDE};'
            f'border-radius:12px;padding:18px 20px;">'
            f'<div style="font-size:12px;color:{GRIS};margin-bottom:12px;">'
            f'COBERTURA DEL SISTEMA</div>'
        )
        for etiqueta, valor in filas_sistema:
            html_sistema += (
                f'<div style="display:flex;justify-content:space-between;'
                f'padding:5px 0;"><span style="font-size:13px;color:{GRIS};">'
                f'{etiqueta}</span><span style="font-size:13px;font-weight:600;'
                f'color:{CLARO};font-variant-numeric:tabular-nums;">{valor}</span>'
                f'</div>'
            )
        st.markdown(html_sistema + "</div>", unsafe_allow_html=True)

    st.write("")
    grafico = load_frame("US500", min(anios, 5))
    if not grafico.empty:
        st.markdown(
            f'<div style="font-size:11.5px;color:{GRIS};letter-spacing:1.2px;'
            f'margin:6px 0 4px 2px;">S&P 500 · EVOLUCIÓN</div>',
            unsafe_allow_html=True,
        )
        st.line_chart(grafico["close"], height=260, color=VERDE)

    st.caption(
        "El sistema no ejecuta operaciones. Genera recomendaciones "
        "documentadas; la decisión y la orden corresponden al operador."
    )


# ------------------------------------------------------------------------- #
# Reparto de capital
# ------------------------------------------------------------------------- #

with tab_reparto:
    st.subheader("Cuánto asignar a cada señal")

    with st.expander("Cómo leer esta pestaña", expanded=False):
        st.markdown(
            """
            #### La pregunta que responde

            El sistema dice «comprar US500» y «vender USOIL». Con 100.000
            dólares, ¿cuánto va a cada una? Repartir a partes iguales sería un
            error: ignora que unas señales son más fiables que otras y que unos
            instrumentos se mueven mucho más que otros.

            #### Las dos cifras que no hay que confundir

            **Riesgo %** es lo que se pierde **si el precio alcanza la
            invalidación**. Es la cifra que de verdad importa. Con un riesgo del
            1% sobre 100.000 dólares, esa operación puede costar 1.000 dólares.

            **% del capital** es cuánto dinero se compromete en la posición.
            Puede ser mucho mayor que el riesgo. Si el stop está un 2% por
            debajo del precio de entrada, arriesgar 1.000 dólares exige
            comprometer 50.000: el 50% del capital para un riesgo del 1%.

            Que el capital comprometido sea alto no significa que se arriesgue
            todo. Significa que el stop está cerca.

            #### De dónde sale el tamaño

            Se parte del **riesgo base por operación** y se ajusta con cuatro
            factores:

            | Factor | Efecto |
            |---|---|
            | **Convicción de la señal** | Una señal fuerte recibe el triple que una débil |
            | **Calidad histórica de la estrategia** | Las que se comportaron mejor reciben algo más, de forma moderada |
            | **Volatilidad del instrumento** | Va implícita en la distancia al stop: un activo más volátil tiene stops más lejanos y por tanto menos unidades |
            | **Correlación con lo ya asignado** | Un instrumento casi idéntico a otro ya elegido recibe menos |

            #### El tope que más protege

            El **riesgo total de la cartera**. Ocho posiciones «prudentes» del
            1,5% suman un 12% de riesgo simultáneo, que no es prudente en
            absoluto. Cuando se alcanza el tope, el sistema deja de asignar
            aunque queden señales buenas.

            #### Una advertencia sobre el histórico

            Dar más capital a la estrategia que mejor lo hizo es «perseguir
            rentabilidad», y está documentado que funciona mal: las estrategias
            alternan periodos buenos y malos. Por eso el ajuste por calidad es
            **deliberadamente moderado** y nunca concentra todo en una sola.
            """
        )

    st.caption(
        "El sistema reparte el capital combinando cuatro factores: la "
        "convicción de la señal, el comportamiento histórico de la estrategia, "
        "la volatilidad del instrumento y su correlación con lo ya asignado."
    )

    r1, r2, r3 = st.columns(3)
    capital_rep = r1.number_input(
        "Capital disponible ($)", 1_000, 100_000_000, 100_000, step=10_000,
        key="capital_reparto",
    )
    riesgo_base = r2.slider(
        "Riesgo base por operación (%)", 0.25, 3.0, 1.0, 0.25,
        help="Porcentaje del capital que se pierde si se toca la "
             "invalidación. Se ajusta después según convicción y calidad.",
    )
    riesgo_total = r3.slider(
        "Riesgo total máximo de la cartera (%)", 2.0, 15.0, 6.0, 0.5,
        help="Tope agregado. Impide que varias posiciones prudentes sumen un "
             "riesgo que no lo es.",
    )

    r4, r5 = st.columns(2)
    conviccion_min = r4.selectbox(
        "Convicción mínima para operar",
        ["Todas", "Moderada o superior", "Sólo fuertes"],
        index=1,
    )
    max_posiciones_rep = r5.slider("Posiciones simultáneas máximas", 3, 15, 8)

    minimo = {
        "Todas": None,
        "Moderada o superior": SignalStrength.MODERATE,
        "Sólo fuertes": SignalStrength.STRONG,
    }[conviccion_min]

    with st.spinner("Calculando el reparto..."):
        objetos = signal_objects(anios)
        historico = compute_backtests(anios)
        puntuaciones = score_strategies(historico) if not historico.empty else {}

        retornos = {}
        for entry in active_catalog():
            frame = load_frame(entry.symbol, min(anios, 3))
            if len(frame) > 90:
                retornos[entry.symbol] = frame["close"].pct_change().dropna()

        reparto = allocate_capital(
            objetos, Decimal(str(capital_rep)),
            strategy_scores=puntuaciones, returns=retornos,
            base_risk_pct=riesgo_base, max_positions=max_posiciones_rep,
            max_total_risk_pct=riesgo_total, min_strength=minimo,
        )

    if not reparto:
        st.info(
            "No hay señales que cumplan los criterios. Con la convicción "
            "mínima exigida, el sistema recomienda no abrir posiciones."
        )
    else:
        resumen = allocation_summary(reparto, Decimal(str(capital_rep)))
        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Posiciones recomendadas", resumen["posiciones"])
        a2.metric("Capital comprometido", f"{resumen['capital_usado_pct']:.1f}%")
        a3.metric("Riesgo total", f"{resumen['riesgo_total_pct']:.2f}%")
        a4.metric(
            "Pérdida máxima estimada", f"${resumen['perdida_maxima']:,.0f}",
            help="Si TODAS las posiciones tocaran su invalidación a la vez.",
        )

        st.dataframe(
            pd.DataFrame([a.to_row() for a in reparto]),
            use_container_width=True, hide_index=True,
        )

        st.download_button(
            "Descargar el reparto en CSV",
            pd.DataFrame([a.to_row() for a in reparto]).to_csv(index=False).encode("utf-8"),
            "reparto_capital.csv", "text/csv",
        )

        if puntuaciones:
            with st.expander("Cómo se ha puntuado cada estrategia"):
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Estrategia": p.strategy,
                            "Sharpe histórico": round(p.sharpe, 2),
                            "Operaciones": p.trades,
                            "Peso relativo": round(p.quality, 2),
                            "Fiabilidad": "Suficiente" if p.reliable else "Escasa",
                            "Comentario": p.explanation,
                        }
                        for p in puntuaciones.values()
                    ]),
                    use_container_width=True, hide_index=True,
                )
                st.caption(
                    "El peso relativo parte de 1,00 como valor neutro. Se "
                    "aplica **contracción hacia el peso igual**: el ajuste por "
                    "histórico existe pero es moderado y nunca concentra todo "
                    "en una estrategia. Dar mucho peso a lo que funcionó en el "
                    "pasado es perseguir rentabilidad, y el pasado predice mal."
                )

        st.warning(
            "**Es una recomendación de tamaño, no una orden.** El operador "
            "decide si ejecuta, con qué tamaño y en qué momento. El riesgo "
            "mostrado supone que la invalidación se respeta."
        )


# ------------------------------------------------------------------------- #
# 0. Mi cuenta — operaciones reales
# ------------------------------------------------------------------------- #

with tab_cuenta:
    diario = get_journal()
    operaciones = diario.all_trades()

    st.subheader("Cuenta real")
    st.caption(
        "Operaciones efectivamente ejecutadas en el bróker. A diferencia del "
        "histórico simulado, esto es dinero real: lo que se operó, a qué "
        "precio y con qué resultado."
    )

    capital_inicial = st.number_input(
        "Capital inicial de la cuenta ($)", 1_000, 100_000_000, 100_000,
        step=1_000, key="capital_real",
    )

    if not operaciones:
        st.info(
            "**Aún no hay operaciones registradas.** Cuando ejecutes una orden "
            "en MetaTrader, regístrala abajo. El sistema calculará tu "
            "resultado real y medirá la diferencia entre lo que recomendó y lo "
            "que realmente hiciste."
        )
    else:
        precios_hoy = precios_actuales()
        resumen = account_summary(operaciones, Decimal(str(capital_inicial)), precios_hoy)

        k1, k2, k3, k4 = st.columns(4)
        k1.metric(
            "Capital actual", f"${resumen['capital_actual']:,.2f}",
            f"{resumen['rentabilidad_pct']:+.2f}%",
        )
        k2.metric("Resultado realizado", f"${resumen['resultado_realizado']:,.2f}")
        k3.metric("Resultado abierto", f"${resumen['resultado_no_realizado']:,.2f}")
        k4.metric(
            "Operaciones",
            f"{resumen['operaciones_cerradas']} cerradas",
            f"{resumen['operaciones_abiertas']} abiertas",
        )

        abiertas = diario.open_trades()
        if abiertas:
            st.markdown("##### Seguimiento de las posiciones abiertas")
            st.caption(
                "Cada posición se reevalúa con los datos de hoy: si la señal "
                "que la justificó sigue vigente, se ha reforzado o se está "
                "debilitando."
            )

            objetos_hoy = {
                (o.symbol, o.strategy): o for o in signal_objects(anios)
            }
            registro_seg = build_registry()
            filas_seg = []

            for pos in abiertas:
                if not pos.strategy:
                    continue
                actual = objetos_hoy.get((pos.symbol, pos.strategy))
                precio = precios_hoy.get(pos.symbol)
                if precio is None:
                    continue
                estrategia_obj = registro_seg.get(pos.strategy)
                if estrategia_obj is None:
                    continue

                original = Signal(
                    signal_id=pos.signal_id or f"manual:{pos.trade_id}",
                    strategy=pos.strategy,
                    strategy_label=estrategia_obj.label,
                    strategy_version=estrategia_obj.version,
                    symbol=pos.symbol,
                    instrument_name=pos.symbol,
                    as_of=datetime.combine(
                        pos.entry_date, datetime.min.time()
                    ).replace(tzinfo=timezone.utc),
                    generated_at=datetime.now(timezone.utc),
                    direction=pos.direction,
                    strength=(actual.strength if actual else SignalStrength.MODERATE),
                    horizon=estrategia_obj.horizon,
                    observed_price=pos.entry_price,
                    entry_price=pos.entry_price,
                    target_price=pos.target_price,
                    stop_price=pos.stop_price,
                    rationale="Posición registrada por el operador",
                )
                filas_seg.append(
                    track_signal(original, actual, precio).to_row()
                )

            if filas_seg:
                st.dataframe(
                    pd.DataFrame(filas_seg),
                    use_container_width=True, hide_index=True,
                )

            st.markdown("##### Detalle de las posiciones abiertas")
            st.dataframe(
                pd.DataFrame([
                    t.to_row(precios_hoy.get(t.symbol)) for t in abiertas
                ])[[
                    "Instrumento", "Dirección", "Unidades", "Precio entrada",
                    "Días", "Resultado $", "Resultado %", "Estrategia",
                ]],
                use_container_width=True, hide_index=True,
            )

            st.markdown("##### Cerrar una posición")
            c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
            a_cerrar = c1.selectbox(
                "Posición", [f"{t.symbol} — {t.trade_id}" for t in abiertas],
            )
            precio_salida = c2.number_input(
                "Precio de salida", 0.0, 10_000_000.0, 0.0, format="%.5f"
            )
            fecha_salida = c3.date_input("Fecha de cierre", value=datetime.now().date())
            motivo = c4.selectbox("Motivo", [r.value for r in ExitReason])

            if st.button("Registrar cierre", type="primary"):
                if precio_salida <= 0:
                    st.error("Introduce el precio de salida.")
                else:
                    tid = a_cerrar.split(" — ", 1)[1]
                    diario.close_trade(
                        tid, fecha_salida, Decimal(str(precio_salida)),
                        ExitReason(motivo),
                    )
                    guardar_copia()
                    st.success("Posición cerrada.")
                    st.rerun()

        cerradas = diario.closed_trades()
        if cerradas:
            st.markdown("##### Operaciones cerradas")
            st.dataframe(
                pd.DataFrame([t.to_row() for t in cerradas]),
                use_container_width=True, hide_index=True,
            )

            st.markdown("##### Resultado por estrategia")
            filas = strategy_performance(operaciones)
            if filas:
                st.dataframe(
                    pd.DataFrame(filas), use_container_width=True, hide_index=True
                )

        st.markdown("##### Calidad de la ejecución")
        st.caption(
            "La diferencia entre lo que el sistema recomendó y lo que "
            "realmente se hizo. Es información que un sistema automático no "
            "puede obtener, porque en él no existe esa brecha."
        )
        q = execution_quality(operaciones)
        if not q["sin_datos"]:
            e1, e2, e3, e4 = st.columns(4)
            e1.metric("Siguieron una señal", f"{q['pct_con_senal']:.0f}%")
            e2.metric(
                "Deslizamiento medio",
                f"{q['deslizamiento_medio_pct']:+.3f}%"
                if q["deslizamiento_medio_pct"] is not None else "—",
                help="Diferencia entre el precio real de entrada y el "
                     "recomendado. Positivo significa peor ejecución.",
            )
            e3.metric(
                "Retraso medio",
                f"{q['retraso_medio_dias']:.1f} días"
                if q["retraso_medio_dias"] is not None else "—",
                help="Días entre la generación de la señal y la ejecución.",
            )
            e4.metric(
                "Disciplina con el stop",
                f"{q['disciplina_stop_pct']:.0f}%"
                if q["disciplina_stop_pct"] is not None else "—",
                help="Porcentaje de cierres que respetaron el nivel de "
                     "invalidación fijado al abrir.",
            )

            if q["operaciones_discrecionales"]:
                col_a, col_b = st.columns(2)
                col_a.metric(
                    "Resultado siguiendo señales",
                    f"${q['resultado_con_senal']:,.2f}",
                )
                col_b.metric(
                    "Resultado por decisión propia",
                    f"${q['resultado_discrecional']:,.2f}",
                )
                st.caption(
                    "Separar ambos permite evaluar el sistema aparte del "
                    "criterio del operador. Con pocas operaciones la "
                    "comparación no es concluyente."
                )

    # ------------------------------------------------------------------ #
    st.divider()
    st.markdown("##### Registrar una operación ejecutada")
    st.caption(
        "Introduce aquí lo que ejecutaste en MetaTrader. Si provino de una "
        "señal del sistema, indícalo: permite medir la diferencia entre lo "
        "recomendado y lo realmente hecho."
    )

    senales_hoy = compute_signals(anios) if not inventory.empty else pd.DataFrame()
    opciones_senal = ["Operación por decisión propia (sin señal)"]
    mapa_senales: dict[str, dict] = {}
    if not senales_hoy.empty:
        operables = senales_hoy[senales_hoy["_direccion"] != "flat"]
        for _, fila in operables.iterrows():
            etiqueta = (
                f"{fila['Instrumento']} · {fila['Señal']} · {fila['Estrategia']}"
            )
            opciones_senal.append(etiqueta)
            mapa_senales[etiqueta] = fila.to_dict()

    origen = st.selectbox("¿De dónde viene la operación?", opciones_senal)
    desde_senal = mapa_senales.get(origen)

    f1, f2, f3 = st.columns(3)
    simbolo_op = f1.text_input(
        "Instrumento",
        value=desde_senal["Instrumento"] if desde_senal else "",
        key="op_symbol",
    )
    direccion_op = f2.selectbox(
        "Dirección", ["COMPRA", "VENTA"],
        index=0 if not desde_senal or desde_senal["Señal"] == "COMPRA" else 1,
    )
    unidades_op = f3.number_input("Unidades", 0.0, 1_000_000.0, 0.0, format="%.4f")

    g1, g2, g3 = st.columns(3)
    precio_op = g1.number_input(
        "Precio de entrada real", 0.0, 10_000_000.0,
        float(desde_senal["Precio entrada"]) if desde_senal else 0.0,
        format="%.5f",
    )
    fecha_op = g2.date_input("Fecha de entrada", value=datetime.now().date())
    ticket_op = g3.text_input("Nº de operación en MT5 (opcional)")

    h1, h2 = st.columns(2)
    stop_op = h1.number_input(
        "Invalidación", 0.0, 10_000_000.0,
        float(desde_senal["Invalidación"] or 0) if desde_senal else 0.0,
        format="%.5f",
    )
    objetivo_op = h2.number_input(
        "Objetivo", 0.0, 10_000_000.0,
        float(desde_senal["Precio objetivo"] or 0) if desde_senal else 0.0,
        format="%.5f",
    )

    notas_op = st.text_input("Notas (opcional)")

    if st.button("Registrar operación", type="primary"):
        if not simbolo_op or unidades_op <= 0 or precio_op <= 0:
            st.error("Instrumento, unidades y precio de entrada son obligatorios.")
        else:
            nueva = RealTrade(
                trade_id=build_trade_id(
                    simbolo_op.upper(), fecha_op, ticket_op.strip()
                ),
                symbol=simbolo_op.upper(),
                direction=Direction.LONG if direccion_op == "COMPRA" else Direction.SHORT,
                units=Decimal(str(unidades_op)),
                entry_date=fecha_op,
                entry_price=Decimal(str(precio_op)),
                stop_price=Decimal(str(stop_op)) if stop_op > 0 else None,
                target_price=Decimal(str(objetivo_op)) if objetivo_op > 0 else None,
                strategy=desde_senal["Estrategia"] if desde_senal else "",
                signal_id=(
                    f"{desde_senal['Estrategia']}:{desde_senal['Instrumento']}:"
                    f"{desde_senal['Fecha dato']}" if desde_senal else None
                ),
                signal_entry_price=(
                    Decimal(str(desde_senal["Precio entrada"])) if desde_senal else None
                ),
                signal_date=(
                    date.fromisoformat(desde_senal["Fecha dato"]) if desde_senal else None
                ),
                notes=notas_op,
                broker_ticket=ticket_op.strip(),
            )
            get_journal().record(nueva)
            guardar_copia()
            st.success(f"Operación registrada: {nueva.trade_id}")
            st.rerun()


# ------------------------------------------------------------------------- #
# 1. Señales de hoy
# ------------------------------------------------------------------------- #

with tab_senales:
    with st.spinner("Calculando señales..."):
        senales = compute_signals(anios)

    if senales.empty:
        st.info("Sin datos suficientes para generar señales.")
    else:
        accionables = senales[senales["_direccion"] != "flat"]
        fecha_dato = senales["Fecha dato"].max()

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Señales de compra", int((senales["_direccion"] == "long").sum()))
        c2.metric("Señales de venta", int((senales["_direccion"] == "short").sum()))
        c3.metric("Sin posición", int((senales["_direccion"] == "flat").sum()))
        c4.metric("Fecha del dato", fecha_dato)

        st.caption(
            "**Precio observado** es el cierre con el que se detectó la señal. "
            "**Precio de entrada** es al que debe abrirse la posición. "
            "**Invalidación** es el nivel que, de alcanzarse, indica que la "
            "hipótesis era incorrecta."
        )

        registro = build_registry()
        etiquetas = ["Todas las estrategias"] + [
            e.label for e in registro.values()
        ]
        elegida = st.selectbox("Estrategia", etiquetas)

        vista = senales
        if elegida != "Todas las estrategias":
            estrategia_obj = next(
                e for e in registro.values() if e.label == elegida
            )
            vista = senales[senales["_estrategia_id"] == estrategia_obj.name]
            st.info(
                f"**{estrategia_obj.label}** — {estrategia_obj.description}\n\n"
                f"**Duración prevista:** {estrategia_obj.horizon.label}  ·  "
                f"**Salida:** {estrategia_obj.exit_rule}"
            )

        f1, f2 = st.columns([2, 1])
        convicciones = f1.multiselect(
            "Nivel de convicción",
            ["strong", "moderate", "weak"],
            default=["strong", "moderate"],
            format_func=lambda v: {
                "strong": "Fuerte", "moderate": "Moderada", "weak": "Débil"
            }[v],
            help="Las señales fuertes son aquellas en que las condiciones de "
                 "la estrategia se cumplen con mayor claridad.",
        )
        solo_operables = f2.checkbox("Sólo operables", value=True)

        if convicciones:
            vista = vista[vista["Convicción"].isin(convicciones)]
        if solo_operables:
            vista = vista[vista["_direccion"] != "flat"]

        if vista.empty:
            st.warning(
                "No hay señales operables con estos filtros. El sistema "
                "recomienda no abrir posiciones."
            )
        else:
            columnas = [
                "Instrumento", "Nombre", "Señal", "Horizonte", "Convicción",
                "Precio observado", "Precio entrada", "Precio objetivo",
                "Invalidación", "Objetivo %", "Riesgo %", "Beneficio/Riesgo",
                "Coste financiación %", "Estrategia", "Salida", "Justificación",
            ]
            st.dataframe(
                vista[columnas].sort_values(
                    ["Estrategia", "Beneficio/Riesgo"], ascending=[True, False]
                ),
                use_container_width=True, hide_index=True,
            )
            st.download_button(
                "Descargar señales en CSV",
                vista[columnas].to_csv(index=False).encode("utf-8"),
                f"senales_{fecha_dato}.csv",
                "text/csv",
            )

        st.divider()
        st.markdown("##### Marcar operaciones para seguimiento")
        st.caption(
            "Marca la casilla de las señales que hayas operado. El sistema "
            "calculará cuánto capital asignarles y las reevaluará cada día."
        )

        capital_seg = st.number_input(
            "Capital de referencia ($)", 1_000, 100_000_000, 100_000,
            step=10_000, key="capital_seguimiento",
            help="Se usa para calcular el tamaño sugerido de cada posición.",
        )

        objetos_disp = {(o.symbol, o.strategy): o for o in signal_objects(anios)}

        if vista.empty:
            st.info("No hay señales operables con los filtros actuales.")
        else:
            # Tamaño sugerido para cada señal visible, calculado con el mismo
            # motor que la pestaña de reparto: así lo que se ve aquí coincide
            # con lo que se recomienda allí.
            historico_seg = compute_backtests(anios)
            puntuaciones_seg = (
                score_strategies(historico_seg) if not historico_seg.empty else {}
            )
            candidatos = [
                objetos_disp[(f["Instrumento"], f["_estrategia_id"])]
                for _, f in vista.iterrows()
                if (f["Instrumento"], f["_estrategia_id"]) in objetos_disp
            ]
            reparto_seg = allocate_capital(
                candidatos, Decimal(str(capital_seg)),
                strategy_scores=puntuaciones_seg,
                max_positions=len(candidatos) or 1,
                max_total_risk_pct=100.0,
            )
            por_simbolo = {(a.symbol, a.strategy): a for a in reparto_seg}

            seguidas = get_watchlist()
            filas_marcado = []
            for _, f in vista.iterrows():
                clave = (f["Instrumento"], f["_estrategia_id"])
                asignacion = por_simbolo.get(clave)
                filas_marcado.append({
                    "Seguir": seguidas.is_watched(*clave),
                    "Instrumento": f["Instrumento"],
                    "Señal": f["Señal"],
                    "Convicción": f["Convicción"],
                    "Horizonte": f["Horizonte"],
                    "Entrada": f["Precio entrada"],
                    "Objetivo": f["Precio objetivo"],
                    "Invalidación": f["Invalidación"],
                    "Capital sugerido $": (
                        round(asignacion.capital_amount, 2) if asignacion else None
                    ),
                    "% del capital": (
                        round(asignacion.capital_pct, 2) if asignacion else None
                    ),
                    "Unidades": (
                        round(asignacion.units, 4) if asignacion else None
                    ),
                    "Riesgo $": (
                        round(
                            float(capital_seg) * asignacion.risk_pct / 100, 2
                        ) if asignacion else None
                    ),
                    "Estrategia": f["Estrategia"],
                    "_id": f["_estrategia_id"],
                })

            editado_seg = st.data_editor(
                pd.DataFrame(filas_marcado).drop(columns=["_id"]),
                use_container_width=True, hide_index=True,
                disabled=[
                    c for c in filas_marcado[0] if c not in ("Seguir", "_id")
                ],
                column_config={
                    "Seguir": st.column_config.CheckboxColumn(
                        "Seguir",
                        help="Marca las señales que hayas operado",
                        width="small",
                    ),
                    "Capital sugerido $": st.column_config.NumberColumn(
                        "Capital sugerido $", format="$%.0f",
                        help="Importe recomendado según convicción, calidad de "
                             "la estrategia y volatilidad del instrumento.",
                    ),
                    "Riesgo $": st.column_config.NumberColumn(
                        "Riesgo $", format="$%.0f",
                        help="Pérdida si el precio alcanza la invalidación.",
                    ),
                },
                key="editor_senales",
            )

            if st.button("Guardar seguimiento", type="primary"):
                añadidas = retiradas = 0
                for indice, fila in editado_seg.iterrows():
                    clave = (fila["Instrumento"], filas_marcado[indice]["_id"])
                    ya_seguida = filas_marcado[indice]["Seguir"]
                    objeto = objetos_disp.get(clave)

                    if fila["Seguir"] and not ya_seguida and objeto:
                        asignacion = por_simbolo.get(clave)
                        nota = (
                            f"Capital sugerido: ${asignacion.capital_amount:,.0f} "
                            f"({asignacion.units:.4f} unidades)"
                            if asignacion else ""
                        )
                        seguidas.add(objeto, nota)
                        añadidas += 1
                    elif not fila["Seguir"] and ya_seguida:
                        for wid, orig, _, _ in seguidas.active_entries():
                            if (orig.symbol, orig.strategy) == clave:
                                seguidas.remove(wid)
                                retiradas += 1

                if añadidas or retiradas:
                    guardar_copia()
                    st.success(
                        f"{añadidas} añadida(s), {retiradas} retirada(s). "
                        f"Ve a «Seguimiento» para ver su evolución."
                    )
                    st.rerun()
                else:
                    st.info("No hay cambios que guardar.")

                st.warning(
            "**Sistema en validación.** Las señales no han pasado por control "
            "de calidad de datos ni por corrección estadística por selección "
            "múltiple. Revisar antes de operar con capital real."
        )


# ------------------------------------------------------------------------- #
# 2. Estrategias
# ------------------------------------------------------------------------- #

with tab_estrategias:
    st.subheader("Motores de estrategia")
    registro = build_registry()
    st.caption(
        f"{len(registro)} estrategias operativas. Cada una analiza los mismos "
        f"mercados con una lógica distinta y un horizonte propio."
    )

    for e in registro.values():
        with st.expander(f"**{e.label}** · {e.horizon.label}"):
            c1, c2 = st.columns([2, 1])
            with c1:
                st.markdown(f"**Qué hace**\n\n{e.description}")
                st.markdown(f"**Cómo se sale**\n\n{e.exit_rule}")
                detalle = (e.__doc__ or "").strip().split("\n\n")
                if len(detalle) > 1:
                    st.markdown(
                        f"**Fundamento**\n\n"
                        f"{' '.join(detalle[1].split())}"
                    )
            with c2:
                st.markdown(
                    f"""
                    **Temporalidad**

                    - Datos analizados: velas **diarias**
                    - Duración prevista: **{e.horizon.label}**
                    - Sesiones estimadas: **{e.horizon.expected_days}**
                    - Historial necesario: **{e.warmup_bars} sesiones**
                    - Paga financiación: **{"Sí" if e.horizon.pays_financing else "No"}**

                    **Versión** {e.version}
                    """
                )

    st.caption(
        "Todas las estrategias analizan velas diarias. Para operar en "
        "temporalidades menores —5 minutos o 1 hora— hace falta conectar el "
        "terminal del bróker, que es el Módulo 01 pendiente."
    )

    st.markdown("##### Comportamiento histórico por estrategia")
    with st.spinner("Ejecutando backtests..."):
        bt = compute_backtests(anios)

    if bt.empty:
        st.info("Sin datos suficientes para evaluar.")
    else:
        resumen = (
            bt.groupby("Estrategia")
            .agg({
                "Rentabilidad anual %": "mean",
                "Sharpe": "mean",
                "Caída máxima %": "mean",
                "Operaciones": "sum",
            })
            .round(2)
            .reset_index()
            .sort_values("Sharpe", ascending=False)
        )
        st.dataframe(resumen, use_container_width=True, hide_index=True)

        with st.expander("Detalle por instrumento"):
            st.dataframe(
                bt.sort_values("Sharpe", ascending=False),
                use_container_width=True, hide_index=True,
            )

        st.markdown("##### Por qué gana o pierde")
        st.caption(
            "Descomposición del resultado de una combinación concreta: cuánto "
            "aporta la lógica de la estrategia y cuánto se llevan los costes."
        )

        d1, d2 = st.columns(2)
        est_analizar = d1.selectbox(
            "Estrategia", [e.label for e in registro.values()], key="attr_est"
        )
        instr_analizar = d2.selectbox(
            "Instrumento", sorted(inventory["symbol"].unique()), key="attr_ins"
        )

        objeto = next(
            (e for e in registro.values() if e.label == est_analizar), None
        )
        frame_attr = load_frame(instr_analizar, anios)
        entrada_attr = next(
            (e for e in active_catalog() if e.symbol == instr_analizar), None
        )

        if objeto and not frame_attr.empty and entrada_attr:
            fin_attr = (
                entrada_attr.instrument.financing
                if isinstance(entrada_attr.instrument, CFD) else None
            )
            try:
                resultado_attr = run_backtest(
                    frame_attr, objeto, instr_analizar, financing=fin_attr
                )
            except ValueError as exc:
                resultado_attr = None
                st.info(f"No se puede analizar: {exc}")

            if resultado_attr and resultado_attr.attribution:
                atr = resultado_attr.attribution
                st.dataframe(
                    pd.DataFrame(atr.to_rows()),
                    use_container_width=True, hide_index=True,
                )

                g1, g2, g3 = st.columns(3)
                g1.metric("Aporte del lado comprado",
                          f"{atr.aporte_largos_pct:+.1f}%")
                g2.metric("Aporte del lado vendido",
                          f"{atr.aporte_cortos_pct:+.1f}%")
                g3.metric(
                    "Concentración",
                    f"{atr.concentracion_top5_pct:.0f}%",
                    help="Porcentaje de todo lo ganado que explican las cinco "
                         "mejores operaciones.",
                )

                salidas = atr.salidas_por_stop + atr.salidas_por_objetivo + atr.salidas_por_senal
                if salidas:
                    st.caption(
                        f"Salidas: {atr.salidas_por_stop} por invalidación, "
                        f"{atr.salidas_por_objetivo} por objetivo, "
                        f"{atr.salidas_por_senal} por cambio de señal."
                    )

                st.markdown("**Diagnóstico**")
                for nota in atr.diagnostico:
                    st.markdown(f"- {nota}")

        st.error(
            "**Advertencia metodológica.** Estos resultados no están corregidos "
            "por selección múltiple. Evaluar decenas de combinaciones garantiza "
            "encontrar algunas buenas por azar. Un ratio alto aquí es un "
            "candidato a validar, no una conclusión."
        )

    st.markdown("##### Estrategias del plan original aún no disponibles")
    st.caption(
        "Se documentan con su motivo para que la ausencia sea una decisión "
        "trazable y no un olvido."
    )
    st.dataframe(
        pd.DataFrame(
            [{"Estrategia": k, "Motivo": v} for k, v in BLOCKED_STRATEGIES.items()]
        ),
        use_container_width=True, hide_index=True,
    )


# ------------------------------------------------------------------------- #
# 3. Cartera
# ------------------------------------------------------------------------- #

with tab_cartera:
    st.subheader("Histórico simulado del sistema")
    st.caption(
        "Reconstrucción sobre datos históricos reales: qué habría pasado "
        "siguiendo las señales del sistema. Sirve para evaluar las estrategias "
        "antes de operarlas. **Las operaciones reales están en «Mi cuenta».**"
    )

    registro_sim = build_registry()
    a, b, c = st.columns(3)
    capital = a.number_input("Capital ($)", 10_000, 10_000_000, 100_000, step=10_000)
    riesgo = b.slider("Riesgo por operación (%)", 0.25, 3.0, 1.0, 0.25)
    max_pos = c.slider("Posiciones simultáneas máx.", 3, 15, 8)

    d1, d2 = st.columns([1, 2])
    conviccion_sim = d1.selectbox(
        "Convicción mínima",
        ["Todas", "Moderada o superior", "Sólo fuertes"],
        index=0,
        help="Simula qué habría pasado operando únicamente las señales que "
             "alcanzaban el nivel de convicción indicado.",
        key="conv_sim",
    )
    elegidas = d2.multiselect(
        "Estrategias a incluir en la simulación",
        [e.label for e in registro_sim.values()],
        default=[e.label for e in registro_sim.values()],
        help="Permite ver cómo se habría comportado cada estrategia por "
             "separado o cualquier combinación de ellas.",
    )

    todos_simbolos = [e.symbol for e in active_catalog()]
    e1, e2 = st.columns([1, 2])
    modo_inst = e1.radio(
        "Instrumentos",
        ["Todos", "Elegir"],
        horizontal=True,
        help="Permite ver la estadística de un solo instrumento o de los que "
             "elijas, en lugar de la cartera completa.",
    )
    if modo_inst == "Elegir":
        simbolos_sim = e2.multiselect(
            "¿Cuáles?",
            todos_simbolos,
            default=todos_simbolos[:2],
            help="Por ejemplo US500 y F40 para ver sólo esos dos.",
        )
    else:
        simbolos_sim = []
        e2.caption(f"Incluyendo los {len(todos_simbolos)} instrumentos del catálogo.")

    if modo_inst == "Elegir" and not simbolos_sim:
        st.warning("Elige al menos un instrumento.")
        st.stop()

    with st.spinner("Reconstruyendo la cartera..."):
        nivel_sim = {
            "Todas": None, "Moderada o superior": "moderate",
            "Sólo fuertes": "strong",
        }[conviccion_sim]
        sim = simulate(
            anios, float(capital), riesgo, max_pos,
            tuple(sorted(elegidas)), nivel_sim,
            tuple(sorted(simbolos_sim)),
        )

    if sim is None or sim.equity_curve.empty:
        st.info("Sin datos suficientes.")
    else:
        m = sim.metrics
        final = float(sim.final_equity)
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("Capital final", f"${final:,.0f}", f"{sim.total_return_pct:+.1f}%")
        k2.metric("Rentabilidad anual", f"{m.get('cagr', 0) * 100:+.2f}%")
        k3.metric("Caída máxima", f"{m.get('max_drawdown', 0) * 100:.1f}%")
        k4.metric("Sharpe", f"{m.get('sharpe', 0):.2f}")

        st.line_chart(sim.equity_curve, height=320)

        st.markdown("##### Posiciones abiertas al final del periodo")
        if sim.open_positions:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Instrumento": p.symbol,
                        "Dirección": "COMPRA" if p.direction.value == "long" else "VENTA",
                        "Unidades": round(float(p.units), 3),
                        "Precio entrada": round(float(p.entry_price), 2),
                        "Precio actual": round(float(p.current_price or 0), 2),
                        "Invalidación": round(float(p.stop_price or 0), 2),
                        "Resultado $": round(float(p.unrealized_pnl), 2),
                        "Resultado %": round(float(p.return_pct), 2),
                        "Estrategia": p.strategy,
                    }
                    for p in sim.open_positions
                ]),
                use_container_width=True, hide_index=True,
            )
        else:
            st.info("Ninguna posición abierta.")

        e1, e2, e3, e4 = st.columns(4)
        e1.metric("Operaciones cerradas", int(m.get("trades", 0)))
        e2.metric("Aciertos", f"{m.get('win_rate', 0) * 100:.1f}%")
        e3.metric("Ganancia media", f"${m.get('avg_win', 0):,.0f}")
        e4.metric("Pérdida media", f"${abs(m.get('avg_loss', 0)):,.0f}")

        st.caption(
            f"Duración media por posición: {m.get('avg_days_held', 0):.0f} días. "
            "Un porcentaje de aciertos bajo con ganancias medias muy superiores "
            "a las pérdidas es el perfil normal del seguimiento de tendencia."
        )

        if sim.trades:
            with st.expander("Últimas 30 operaciones"):
                st.dataframe(
                    pd.DataFrame([t.to_row() for t in sim.trades[-30:]][::-1]),
                    use_container_width=True, hide_index=True,
                )

        st.warning(
            "**Reconstrucción histórica.** Los precios son reales, pero las "
            "operaciones no se ejecutaron: el sistema calcula qué habría pasado. "
            "No incluye deslizamiento ni huecos de apertura. El seguimiento de "
            "operaciones reales corresponde al Módulo 13, aún por construir."
        )


# ------------------------------------------------------------------------- #
# 4. Mercados
# ------------------------------------------------------------------------- #

with tab_mercados:
    disponibles = sorted(inventory["symbol"].unique())
    simbolo = st.selectbox("Instrumento", disponibles)
    serie = load_frame(simbolo, anios)

    if serie.empty:
        st.info("Sin datos para este instrumento.")
    else:
        entrada = next(
            (e for e in active_catalog() if e.symbol == simbolo), None
        )
        nombre = entrada.instrument.name if entrada else simbolo
        st.subheader(f"{simbolo} — {nombre}")

        cierre = serie["close"]
        variacion = (cierre.iloc[-1] / cierre.iloc[0] - 1) * 100
        rets = cierre.pct_change().dropna()
        vol = rets.std() * (252 ** 0.5) * 100
        caida = ((cierre / cierre.cummax() - 1).min()) * 100

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Último cierre", f"{cierre.iloc[-1]:,.2f}")
        m2.metric(f"Variación {anios} años", f"{variacion:+.1f}%")
        m3.metric("Volatilidad anual", f"{vol:.1f}%")
        m4.metric("Caída máxima", f"{caida:.1f}%")

        vista = st.radio(
            "Tipo de gráfico", ["Línea", "Velas"], horizontal=True,
            label_visibility="collapsed",
        )

        if vista == "Velas":
            # Las velas sólo se muestran en el detalle de un instrumento. En
            # una rejilla de seis mercados no se leen y sólo añaden ruido.
            recorte = serie.tail(120)
            figura = go.Figure(
                data=[
                    go.Candlestick(
                        x=recorte.index,
                        open=recorte["open"], high=recorte["high"],
                        low=recorte["low"], close=recorte["close"],
                        increasing_line_color=VERDE, increasing_fillcolor=VERDE,
                        decreasing_line_color=ROJO, decreasing_fillcolor=ROJO,
                        line=dict(width=1),
                    )
                ]
            )
            figura.update_layout(
                height=380, margin=dict(l=0, r=0, t=10, b=0),
                paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor=PANEL,
                font=dict(color=GRIS, size=11),
                xaxis=dict(gridcolor=BORDE, rangeslider=dict(visible=False)),
                yaxis=dict(gridcolor=BORDE, side="right"),
                showlegend=False,
            )
            st.plotly_chart(figura, use_container_width=True)
            st.caption("Últimas 120 sesiones.")
        else:
            st.line_chart(cierre, height=340, color=VERDE)

        with st.expander("Verificar los indicadores que usa el sistema"):
            st.caption(
                "Estos son los valores exactos con los que se calculan las "
                "señales. Sirven para contrastarlos con los del terminal del "
                "bróker."
            )
            entrada_v = next(
                (e for e in active_catalog() if e.symbol == simbolo), None
            )
            fuente_v = active_source()
            ticker_v = entrada_v.yahoo_symbol if entrada_v else "—"

            indicadores = {
                "Último cierre": float(cierre.iloc[-1]),
                "Media 20 sesiones": float(fx.sma(cierre, 20).iloc[-1]),
                "Media 50 sesiones": float(fx.sma(cierre, 50).iloc[-1]),
                "Media 200 sesiones": float(fx.sma(cierre, 200).iloc[-1])
                    if len(cierre) >= 200 else None,
                "Volatilidad anualizada %": float(
                    fx.realized_volatility(cierre, 20).iloc[-1] * 100
                ),
                "ATR 14": float(
                    fx.atr(serie["high"], serie["low"], cierre, 14).iloc[-1]
                ),
            }
            st.dataframe(
                pd.DataFrame([
                    {"Indicador": k, "Valor": round(v, 4) if v else None}
                    for k, v in indicadores.items()
                ]),
                use_container_width=True, hide_index=True,
            )

            if simbolo in METALES_USAN_FUTUROS:
                st.warning(
                    f"**{simbolo} usa el precio del futuro, no del contado.** "
                    f"Yahoo no publica el contado de los metales con un "
                    f"identificador estable, así que se usa el futuro del "
                    f"COMEX (`{ticker_v}`).\n\n"
                    f"El futuro cotiza con una prima sobre el contado, porque "
                    f"incorpora el coste de financiación hasta el vencimiento. "
                    f"**Los niveles de precio y las medias no coincidirán con "
                    f"los de MetaTrader**, aunque la tendencia y los cruces sí "
                    f"se comportan igual: ambas series siguen al mismo metal.\n\n"
                    f"Se resuelve conectando el terminal del bróker, que es el "
                    f"módulo pendiente."
                )

            st.info(
                f"**Fuente de estos datos:** `{ticker_v}` "
                f"({fuente_v.value if fuente_v else '—'}), con "
                f"{len(serie)} sesiones cargadas.\n\n"
                f"Si estos números no coinciden con los de MetaTrader, es "
                f"porque el bróker usa su propio feed. Ambos siguen al mismo "
                f"activo pero con precios ligeramente distintos: horarios de "
                f"cierre, diferencial y proveedor no son los mismos. La "
                f"diferencia es esperable; una discrepancia grande y "
                f"sostenida no lo es y conviene reportarla."
            )

        with st.expander("Últimas 15 sesiones"):
            st.dataframe(
                serie.tail(15).sort_index(ascending=False),
                use_container_width=True,
            )


# ------------------------------------------------------------------------- #
# 5. Catálogo
# ------------------------------------------------------------------------- #

with tab_catalogo:
    st.subheader("Universo operable")
    st.caption(
        "Aquí se decide qué instrumentos analiza el sistema. Los cambios se "
        "guardan aparte, sin modificar el catálogo base auditado."
    )

    capa = CatalogOverlay.load()
    estado = pd.DataFrame(full_catalog_status(capa))

    activos = int(estado["activo"].sum())
    c1, c2, c3 = st.columns(3)
    c1.metric("Instrumentos activos", activos)
    c2.metric("Desactivados", len(estado) - activos)
    c3.metric("Añadidos por el operador",
              int((estado["origen"] != "Catálogo base").sum()))

    st.markdown("##### Activar o desactivar instrumentos")
    st.caption(
        "Desactivar no borra nada: el instrumento deja de analizarse y puede "
        "reactivarse cuando se quiera."
    )

    editado = st.data_editor(
        estado.rename(columns={
            "symbol": "Símbolo", "name": "Nombre", "group": "Categoría",
            "yahoo": "Datos", "mt5": "Bróker", "activo": "Activo",
            "origen": "Origen",
        }),
        use_container_width=True, hide_index=True,
        disabled=["Símbolo", "Nombre", "Categoría", "Datos", "Bróker", "Origen"],
        column_config={
            "Activo": st.column_config.CheckboxColumn(
                "Activo", help="Desmarcar para excluirlo del análisis"
            )
        },
        key="editor_catalogo",
    )

    if st.button("Guardar cambios", type="primary"):
        nueva = CatalogOverlay.load()
        for _, fila in editado.iterrows():
            if fila["Activo"]:
                nueva.disabled.discard(fila["Símbolo"])
            else:
                nueva.disabled.add(fila["Símbolo"])
        nueva.save()
        st.cache_data.clear()
        st.success("Catálogo actualizado.")
        st.rerun()

    st.divider()
    st.markdown("##### Añadir un instrumento nuevo")
    st.caption(
        "El identificador debe existir en Yahoo Finance. Ejemplos: `BTC-USD` "
        "para Bitcoin, `^VIX` para el índice de volatilidad, `AAPL` para Apple."
    )

    n1, n2, n3 = st.columns(3)
    nuevo_symbol = n1.text_input("Símbolo interno", placeholder="BTCUSD")
    nuevo_nombre = n2.text_input("Nombre", placeholder="Bitcoin")
    nuevo_yahoo = n3.text_input("Identificador de datos", placeholder="BTC-USD")

    n4, n5 = st.columns(2)
    nueva_categoria = n4.text_input("Categoría", placeholder="Cripto")
    nuevo_mt5 = n5.text_input("Símbolo en el bróker (opcional)", placeholder="BTCUSD")

    if st.button("Añadir instrumento"):
        if not (nuevo_symbol and nuevo_nombre and nuevo_yahoo):
            st.error("Símbolo, nombre e identificador de datos son obligatorios.")
        else:
            capa_nueva = CatalogOverlay.load()
            capa_nueva.add_instrument(
                nuevo_symbol, nuevo_nombre, nuevo_yahoo,
                nueva_categoria or "Otros", mt5_symbol=nuevo_mt5 or None,
            )
            st.cache_data.clear()
            st.success(
                f"{nuevo_symbol.upper()} añadido. Pulsa «Actualizar datos ahora» "
                f"en el lateral para descargar su histórico."
            )
            st.rerun()

    añadidos = [f for f in full_catalog_status() if f["origen"] != "Catálogo base"]
    if añadidos:
        st.markdown("##### Eliminar un instrumento añadido")
        a_borrar = st.selectbox(
            "Instrumento", [f["symbol"] for f in añadidos], key="borrar"
        )
        if st.button("Eliminar"):
            capa_nueva = CatalogOverlay.load()
            capa_nueva.remove_added(a_borrar)
            st.cache_data.clear()
            st.rerun()


# ------------------------------------------------------------------------- #
# 6. Sistema
# ------------------------------------------------------------------------- #

with tab_sistema:
    st.subheader("Estado de los datos")
    repo = get_repo()

    s1, s2, s3, s4 = st.columns(4)
    s1.metric("Instrumentos con datos", len(inventory))
    s2.metric("Barras almacenadas", f"{repo.total_bars():,}")
    cobertura = (
        inventory["last_ts"].max() - inventory["first_ts"].min()
    ).days / 365.25
    s3.metric("Histórico disponible", f"{cobertura:.1f} años")
    s4.metric(
        "Revisiones del proveedor", repo.revision_count(),
        help="Veces que el proveedor modificó datos históricos ya almacenados. "
             "El sistema lo detecta y conserva el valor anterior.",
    )

    vista = inventory.copy()
    vista["Desde"] = vista["first_ts"].dt.date
    vista["Hasta"] = vista["last_ts"].dt.date
    st.dataframe(
        vista.rename(columns={"symbol": "Instrumento", "bars": "Barras",
                              "source": "Fuente"})[
            ["Instrumento", "Fuente", "Barras", "Desde", "Hasta"]
        ],
        use_container_width=True, hide_index=True,
    )

    st.markdown("##### Copias de seguridad de tus datos")
    nivel_sistema = detect_persistence(DB_PATH)

    st.caption(
        "Los precios se regeneran solos descargándolos de nuevo. Las señales "
        "en seguimiento y las operaciones registradas no: son datos tuyos y "
        "sólo existen aquí."
    )

    estado_remoto = remote_status()
    if estado_remoto["configurada"]:
        if estado_remoto["conectada"]:
            st.success(
                f"**Base de datos externa conectada.** Tus datos se conservan "
                f"aunque la aplicación se reinicie.\n\n"
                f"`{estado_remoto['url']}` · "
                f"{estado_remoto['seguimiento']} señal(es) en seguimiento, "
                f"{estado_remoto['operaciones']} operación(es) almacenadas."
            )
        else:
            st.error(
                f"**Base de datos externa configurada pero sin conexión.** "
                f"El sistema está funcionando con almacenamiento local.\n\n"
                f"Detalle: {estado_remoto.get('error', 'desconocido')}"
            )

    b1, b2 = st.columns([1, 1])
    with b1:
        st.markdown(f"**Almacenamiento:** {nivel_sistema.value}")
        if nivel_sistema.warning:
            (st.error if not nivel_sistema.is_durable else st.info)(
                nivel_sistema.warning
            )
        else:
            st.success(
                "Base de datos externa configurada. Los datos sobreviven a "
                "cualquier reinicio."
            )

    with b2:
        info_copia = backup_info()
        if info_copia["existe"]:
            fecha_copia = info_copia.get("fecha")
            st.markdown(
                f"**Última copia automática**\n\n"
                f"{fecha_copia.strftime('%d/%m/%Y %H:%M') if fecha_copia else '—'} UTC\n\n"
                f"{info_copia['seguimiento']} señal(es) · "
                f"{info_copia['operaciones']} operación(es) · "
                f"{info_copia['generaciones']} generación(es) conservada(s)"
            )
        else:
            st.markdown(
                "**Sin copia automática todavía.**\n\nSe creará en cuanto "
                "marques una señal o registres una operación."
            )

    copia_actual = export_user_data(DB_PATH)
    d1, d2 = st.columns(2)

    d1.download_button(
        "Descargar mis datos",
        copia_actual.to_json().encode("utf-8"),
        suggested_filename(),
        "application/json",
        help="Guarda este archivo. Sirve para restaurar tu seguimiento si se "
             "pierde, y como copia de seguridad.",
        disabled=not (copia_actual.watchlist or copia_actual.trades),
    )
    d2.caption(
        f"Contenido actual: {len(copia_actual.watchlist)} señal(es) en "
        f"seguimiento, {len(copia_actual.trades)} operación(es)."
    )

    subido = st.file_uploader(
        "Restaurar desde un archivo descargado", type=["json"],
        help="Sube aquí el archivo que descargaste antes.",
    )
    if subido is not None:
        try:
            recuperada = UserDataSnapshot.from_json(subido.getvalue())
        except ValueError as exc:
            st.error(str(exc))
        else:
            st.info(f"Archivo válido: {recuperada.summary}")
            sustituir = st.checkbox(
                "Sustituir lo que hay ahora en lugar de fusionarlo",
                help="Fusionar conserva lo actual y añade lo del archivo. "
                     "Restaurar el mismo archivo dos veces no duplica nada.",
            )
            if st.button("Restaurar", type="primary"):
                resultado = import_user_data(
                    DB_PATH, recuperada, replace=sustituir
                )
                st.cache_resource.clear()
                st.success(
                    f"Restaurado: {resultado['seguimiento']} señal(es) y "
                    f"{resultado['operaciones']} operación(es)."
                )
                st.rerun()

    if not nivel_sistema.is_durable:
        with st.expander("Cómo conservar los datos de forma permanente"):
            st.markdown(
                """
                Esta aplicación corre en un servicio que **borra el disco cada
                vez que se reinicia o se despliega**. Es una limitación del
                alojamiento gratuito, no un fallo del sistema.

                Hay tres formas de convivir con ello, de menor a mayor
                fiabilidad:

                **1. Descargar tus datos** con el botón de arriba, y volver a
                subirlos si se pierden. Funciona siempre y no requiere nada
                más, pero depende de que te acuerdes.

                **2. Copia automática.** El sistema guarda una copia cada vez
                que marcas una señal, y la restaura sola al arrancar. Cubre los
                reinicios cortos, pero no un despliegue nuevo, que también
                borra el directorio temporal.

                **3. Base de datos externa.** Es la solución definitiva.
                Configurando la variable de entorno `QQ_USER_DATA_URL` con la
                dirección de una base de datos PostgreSQL —hay servicios
                gratuitos como Supabase o Neon— los datos dejan de depender del
                servidor de la aplicación.

                Mientras tanto, la recomendación práctica: **descarga tus datos
                al terminar cada sesión de trabajo.**
                """
            )

    st.divider()
    st.markdown("##### Coste de mantener posiciones abiertas")
    st.caption(
        "Medido de la especificación de contrato del bróker. Determina la "
        "rentabilidad real de cualquier estrategia que mantenga posiciones "
        "más de un día."
    )

    filas = []
    for e in active_catalog():
        fin = getattr(e.instrument, "financing", None)
        if fin is None:
            continue
        filas.append({
            "Instrumento": e.symbol,
            "Nombre": e.instrument.name,
            "Coste anual comprado %": round(fin.annual_pct(True), 2),
            "Ingreso anual vendido %": round(fin.annual_pct(False), 2),
            "Origen": "Medido en el bróker" if fin.measured else "Estimado",
        })

    if filas:
        tabla = pd.DataFrame(filas)
        st.dataframe(tabla, use_container_width=True, hide_index=True)
        medidos = int((tabla["Origen"] == "Medido en el bróker").sum())
        st.info(
            "**Asimetría estructural:** mantener posiciones compradas cuesta "
            "dinero; mantener posiciones vendidas lo genera. No es una opinión "
            "sobre el mercado, es la estructura de comisiones del bróker."
        )
        if medidos < len(tabla):
            st.warning(
                f"Sólo {medidos} de {len(tabla)} instrumentos tienen el coste "
                f"medido en el terminal. El resto usa una estimación "
                f"conservadora, pendiente de verificar."
            )

st.divider()
st.caption(
    "QQ Quant OS · El sistema no ejecuta operaciones: las decisiones y su "
    "ejecución corresponden al operador"
)


# ------------------------------------------------------------------------- #
# Diversificación por riesgo sobre las señales EN SEGUIMIENTO (v1.13)
# ------------------------------------------------------------------------- #

with tab_reparto:
    st.divider()
    st.subheader("Cuál de tus operaciones merece más capital")
    st.caption(
        "Toma las señales que TÚ has marcado en seguimiento, las ordena por "
        "probabilidad de alcanzar su objetivo, y reparte el riesgo entre "
        "ellas. Puede concluir que sólo una merece capital hoy y que conviene "
        "esperar mejores entradas."
    )

    lista_rep = get_watchlist()

    if not lista_rep:
        st.info(
            "**No tienes nada en seguimiento.** Ve a «Señales de hoy», marca "
            "las operaciones que te interesen, y vuelve aquí. El motor "
            "trabajará sobre esas."
        )
    else:
        r1, r2, r3 = st.columns(3)
        capital_rep = r1.number_input(
            "Tu capital ($)", 1_000, 10_000_000, 100_000, step=10_000,
            key="cap_rep",
        )
        perfil_rep = r2.selectbox(
            "Perfil de riesgo", ["Conservador", "Moderado", "Agresivo"],
            index=1, key="perf_rep",
        )
        reserva_rep = r3.slider(
            "Reserva para mañana (%)", 0, 70, 40, 5, key="res_rep",
            help="Riesgo que NO se reparte hoy, para poder tomar señales "
                 "mejores mañana.",
        )

        if st.button("Analizar mis operaciones", type="primary"):
            limites = PERFILES[{
                "Conservador": RiskProfile.CONSERVATIVE,
                "Moderado": RiskProfile.MODERATE,
                "Agresivo": RiskProfile.AGGRESSIVE,
            }[perfil_rep]].validated()

            cierres = {}
            for entry in active_catalog():
                f = load_frame(entry.symbol, anios)
                if not f.empty:
                    cierres[entry.symbol] = f["close"].pct_change()
            matriz = (
                pd.DataFrame(cierres).dropna().corr() if len(cierres) > 1 else None
            )
            grupos = build_clusters(matriz) if matriz is not None else {}

            precios_hoy = {}
            for entry in active_catalog():
                f = load_frame(entry.symbol, anios)
                if not f.empty:
                    precios_hoy[entry.symbol] = Decimal(str(f["close"].iloc[-1]))

            filas_prob, candidatas = [], []
            for ident, sig, nota, dia in lista_rep:
                precio = precios_hoy.get(sig.symbol, sig.entry_price)
                prob = estimate_target_probability(
                    entry_price=sig.entry_price,
                    current_price=precio,
                    stop_price=sig.stop_price or sig.entry_price,
                    target_price=sig.target_price,
                    direction=sig.direction.value,
                )
                if prob is None:
                    filas_prob.append({
                        "Instrumento": sig.symbol,
                        "Estrategia": sig.strategy,
                        "Prob. objetivo": None,
                        "B/R": None,
                        "Valor esperado": None,
                        "Confianza": "sin objetivo definido",
                    })
                    continue

                filas_prob.append({
                    "Instrumento": sig.symbol,
                    "Estrategia": sig.strategy,
                    "Prob. objetivo": f"{prob.probability * 100:.0f}%",
                    "B/R": round(prob.reward_risk, 2),
                    "Valor esperado": round(prob.expected_value_r, 2),
                    "Avance": f"{prob.progress_pct:.0f}%",
                    "Confianza": prob.confidence.label,
                })

                if sig.stop_price and abs(precio - sig.stop_price) > 0:
                    candidatas.append(Candidate(
                        signal_id=ident,
                        symbol=sig.symbol,
                        strategy=sig.strategy,
                        direction=sig.direction.value,
                        # La puntuación es la probabilidad de llegar al
                        # objetivo, escalada a 0-100. Es lo que se pidió: que
                        # ordene por probabilidad de alcanzar el TP.
                        quality_score=prob.probability * 100.0,
                        entry_price=precio,
                        stop_price=sig.stop_price,
                        times_seen=1,
                        cluster=grupos.get(sig.symbol, "individual"),
                    ))

            st.markdown("##### Probabilidad de alcanzar el objetivo")
            st.dataframe(
                pd.DataFrame(filas_prob), use_container_width=True,
                hide_index=True,
            )
            st.caption(
                "La probabilidad combina la geometría de la operación "
                "—distancia al objetivo frente a distancia al stop— con el "
                "histórico cuando lo hay. **Valor esperado** positivo "
                "significa que la relación beneficio/riesgo compensa la "
                "probabilidad. Es una estimación para ordenar operaciones "
                "entre sí, no una predicción."
            )

            if not candidatas:
                st.warning(
                    "Ninguna de tus operaciones tiene objetivo e invalidación "
                    "definidos, así que no se puede repartir riesgo."
                )
            else:
                plan = allocate_by_risk(
                    candidatas, Decimal(str(capital_rep)), limites,
                    correlations=matriz,
                    reserve_pct=Decimal(str(reserva_rep)),
                )

                st.markdown("##### Reparto recomendado")
                if plan.accepted:
                    st.success(plan.headline)
                else:
                    st.warning(plan.headline)

                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Reciben capital", f"{len(plan.accepted)} de {len(candidatas)}")
                k2.metric("Riesgo usado", f"{plan.used_pct:.2f}%")
                k3.metric("Reservado", f"{plan.reserved_pct:.2f}%")
                k4.metric(
                    "Apuestas reales", f"{plan.effective_bets:.1f}",
                    delta=f"de {plan.nominal_bets} posiciones", delta_color="off",
                    help="Posiciones correlacionadas cuentan como menos de una "
                         "apuesta independiente.",
                )

                st.dataframe(
                    plan.to_frame(), use_container_width=True, hide_index=True
                )

                descartadas = [a for a in plan.allocations if not a.decision.is_accept]
                if descartadas:
                    st.markdown("##### Las que conviene esperar, y por qué")
                    for a in descartadas:
                        with st.expander(
                            f"⏸ {a.candidate.symbol} · {a.candidate.strategy} · "
                            f"{a.decision.value}"
                        ):
                            st.write(a.explanation)

                st.markdown("##### Las que reciben capital")
                for a in plan.accepted:
                    with st.expander(
                        f"✅ {a.rank}. {a.candidate.symbol} · "
                        f"{float(a.risk_pct):.3f}% de riesgo · "
                        f"${float(a.risk_amount):,.0f}"
                    ):
                        st.write(a.explanation)

# ------------------------------------------------------------------------- #
# Asistente: cerebro flotante y chat en cascada (gratis -> IA)
# ------------------------------------------------------------------------- #

st.markdown(
    """
    <style>
    @keyframes qqflota {
      0%   { transform: translateY(0)     rotate(-3deg) scale(1);    }
      50%  { transform: translateY(-12px) rotate(3deg)  scale(1.05); }
      100% { transform: translateY(0)     rotate(-3deg) scale(1);    }
    }
    @keyframes qqpulso {
      0%   { box-shadow: 0 0 0 0 rgba(99,179,237,.55); }
      70%  { box-shadow: 0 0 0 24px rgba(99,179,237,0); }
      100% { box-shadow: 0 0 0 0 rgba(99,179,237,0); }
    }
    #qq-cerebro {
      position: fixed; left: 24px; bottom: 86px; z-index: 998;
      width: 64px; height: 64px; border-radius: 50%;
      display: flex; align-items: center; justify-content: center;
      font-size: 32px; pointer-events: none;
      background: radial-gradient(circle at 32% 26%, #8fdcff 0%, #3b6fd4 60%, #16264f 100%);
      animation: qqflota 3.8s ease-in-out infinite, qqpulso 2.8s infinite;
    }
    #qq-cerebro-txt {
      position: fixed; left: 96px; bottom: 102px; z-index: 998;
      font-size: 11px; letter-spacing: .12em; color: #8fb6e8;
      text-transform: uppercase; pointer-events: none; font-weight: 600;
    }
    </style>
    <div id="qq-cerebro">&#129504;</div>
    <div id="qq-cerebro-txt">Asistente</div>
    """,
    unsafe_allow_html=True,
)


def construir_snapshot() -> Snapshot:
    """Lee el estado real del sistema para el asistente.

    Todo lo que el asistente puede decir sale de aquí. Si un dato no se puede
    leer, se omite y la respuesta lo declara, en lugar de suplirlo.
    """
    snap = Snapshot(fecha=ultima.date().isoformat())

    try:
        _df = compute_signals(anios)
        if not _df.empty:
            snap.senales = _df.to_dict("records")
    except Exception:  # noqa: BLE001
        pass

    try:
        snap.instrumentos = [e.symbol for e in active_catalog()]
        snap.estrategias = [
            {
                "nombre": e.label,
                "descripcion": e.description,
                "salida": e.exit_rule,
                "horizonte": e.horizon.value,
            }
            for e in build_registry().values()
        ]
    except Exception:  # noqa: BLE001
        pass

    try:
        for entry in active_catalog():
            t = financing_from_instrument(entry.instrument)
            if t is not None:
                snap.financiacion[entry.symbol] = {
                    "largo": float(t.annual_rate_long) * 100,
                    "corto": float(t.annual_rate_short) * 100,
                    "medido": bool(t.measured),
                }
    except Exception:  # noqa: BLE001
        pass

    try:
        snap.barras_totales = get_repo().total_bars()
    except Exception:  # noqa: BLE001
        pass

    try:
        _lista = get_watchlist()
        _precios = {}
        for entry in active_catalog():
            _f = load_frame(entry.symbol, anios)
            if not _f.empty:
                _precios[entry.symbol] = Decimal(str(_f["close"].iloc[-1]))
        for _id, _s, _n, _d in _lista:
            _p = _precios.get(_s.symbol, _s.entry_price)
            _res = float((_p - _s.entry_price) / _s.entry_price * 100)
            if _s.direction.value == "short":
                _res = -_res
            snap.seguimiento.append({
                "Instrumento": _s.symbol,
                "Estrategia": _s.strategy,
                "Estado": "En curso",
                "Resultado %": round(_res, 2),
                "Días": (ultima.date() - _d).days,
                "Mensaje": _s.rationale[:80],
            })
    except Exception:  # noqa: BLE001
        pass

    return snap


with st.sidebar:
    st.divider()
    st.markdown("### 🧠 Asistente")

    if "qq_chat" not in st.session_state:
        st.session_state.qq_chat = []

    _ia = Assistant()
    st.caption(
        "Pregúntame sobre señales, tu seguimiento, el reparto o cualquier "
        "concepto. Escribe **ayuda** para ver todo lo que sé."
        + ("" if _ia.is_configured else "  \n_Modo gratuito: sólo preguntas conocidas._")
    )

    for _t in st.session_state.qq_chat[-8:]:
        with st.chat_message(_t["role"]):
            st.markdown(_t["content"])

    _pregunta = st.chat_input("¿Qué quieres saber?")
    if _pregunta:
        st.session_state.qq_chat.append({"role": "user", "content": _pregunta})
        with st.chat_message("user"):
            st.markdown(_pregunta)

        with st.chat_message("assistant"):
            with st.spinner("Consultando el sistema..."):
                _snap = construir_snapshot()
                # 1) Camino gratuito: instantáneo y sin coste.
                _r = responder(_pregunta, _snap)

                # 2) Sólo si no se reconoció y hay clave, se paga por la IA.
                if _r is None and _ia.is_configured:
                    _ctx = SystemContext(
                        fecha=_snap.fecha,
                        senales_hoy=str(_snap.senales[:20])[:3000],
                        seguimiento=str(_snap.seguimiento)[:2000],
                        instrumentos=", ".join(_snap.instrumentos),
                        estrategias=", ".join(
                            e["nombre"] for e in _snap.estrategias
                        ),
                        avisos=[
                            "El sistema evaluó 198 combinaciones; parte de la "
                            "ventaja histórica puede deberse al azar.",
                            "6 de 23 instrumentos tienen coste de financiación "
                            "estimado, no medido.",
                            "El sistema NO ejecuta operaciones.",
                        ],
                    )
                    try:
                        _r = _ia.ask(_pregunta, _ctx, st.session_state.qq_chat[:-1])
                    except AssistantError as _e:
                        _r = f"No pude responder: {_e}"

                if _r is None:
                    _r = (
                        "No he entendido esa pregunta. Esto es lo que sí sé "
                        "hacer:\n\n" + AYUDA
                    )
            st.markdown(_r)

        st.session_state.qq_chat.append({"role": "assistant", "content": _r})

    if st.session_state.qq_chat and st.button("Borrar conversación"):
        st.session_state.qq_chat = []
        st.rerun()
