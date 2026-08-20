"""Genera un informe HTML autocontenido con el estado del sistema.

Uso:

    python scripts/generar_informe.py

Produce `informe_qq_quant_os.html`: un único archivo con los gráficos y las
tablas incrustados. Se abre en cualquier navegador sin instalar nada y se
puede adjuntar a un correo.

POR QUÉ UN ARCHIVO ÚNICO
------------------------
El panel de Streamlit sólo es accesible desde la máquina que lo ejecuta. Para
compartir resultados con alguien remoto hacen falta o un despliegue en
servidor, o un artefacto portable. Este script produce lo segundo: los
gráficos se incrustan como SVG y las tablas como HTML, de modo que el archivo
no depende de internet ni de ningún recurso externo.
"""

from __future__ import annotations

import base64
import io
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from qq_core.backtest.engine import run_backtest  # noqa: E402
from qq_core.catalog.overlay import active_catalog
from qq_core.catalog import instruments as catalog  # noqa: E402
from qq_core.domain.enums import Timeframe  # noqa: E402
from qq_core.domain.instrument import CFD  # noqa: E402
from qq_core.features.engine import bars_to_frame  # noqa: E402
from qq_core.storage.sqlite_repository import SQLiteBarRepository  # noqa: E402
from qq_core.portfolio.risk import RiskConfig  # noqa: E402
from qq_core.portfolio.simulator import simulate_portfolio  # noqa: E402
from qq_core.signals.service import generate_signals, load_universe  # noqa: E402
from qq_core.strategies.library import build_registry, STRATEGY_INFO  # noqa: E402

AZUL = "#14304f"
GRIS = "#6b7787"

plt.rcParams.update(
    {
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "axes.edgecolor": "#dfe4eb",
        "axes.labelcolor": GRIS,
        "xtick.color": GRIS,
        "ytick.color": GRIS,
        "font.size": 9,
        "axes.spines.top": False,
        "axes.spines.right": False,
    }
)


def figura_a_svg(fig) -> str:
    """Convierte una figura de matplotlib en SVG incrustable."""
    buffer = io.StringIO()
    fig.savefig(buffer, format="svg", bbox_inches="tight")
    plt.close(fig)
    svg = buffer.getvalue()
    return svg[svg.index("<svg") :]


def grafico_precio(serie: pd.Series, titulo: str) -> str:
    fig, ax = plt.subplots(figsize=(9, 2.9))
    ax.plot(serie.index, serie.values, color=AZUL, linewidth=1.1)
    ax.set_title(titulo, color=AZUL, fontsize=11, loc="left", pad=10)
    ax.grid(axis="y", color="#eef1f5")
    ax.margins(x=0)
    return figura_a_svg(fig)


def grafico_equity(serie: pd.Series, inicial: float) -> str:
    """Evolución del capital de la cartera simulada."""
    fig, ax = plt.subplots(figsize=(9, 3.2))
    ax.plot(serie.index, serie.values, color=AZUL, linewidth=1.4)
    ax.axhline(inicial, color="#b8722c", linewidth=1, linestyle="--")
    ax.fill_between(serie.index, inicial, serie.values,
                    where=(serie.values >= inicial), color=AZUL, alpha=0.10)
    ax.fill_between(serie.index, inicial, serie.values,
                    where=(serie.values < inicial), color="#b8722c", alpha=0.10)
    ax.set_ylabel("Capital ($)")
    ax.grid(axis="y", color="#eef1f5")
    ax.margins(x=0)
    return figura_a_svg(fig)


def grafico_cobertura(inventario: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(9, max(3.2, len(inventario) * 0.22)))
    ax.barh(inventario["symbol"], inventario["bars"], color=AZUL, height=0.6)
    ax.set_xlabel("Barras de precio almacenadas")
    ax.grid(axis="x", color="#eef1f5")
    ax.invert_yaxis()
    ax.tick_params(labelsize=7.5)
    return figura_a_svg(fig)


def tabla_html(df: pd.DataFrame) -> str:
    return df.to_html(index=False, border=0, classes="tabla", justify="left")


def main() -> int:
    db = Path("qq_data.db")
    if not db.exists():
        print("\nNo existe 'qq_data.db'. Ejecuta primero:")
        print("   python scripts/descargar_datos.py\n")
        return 1

    repo = SQLiteBarRepository(db)
    fuente = repo.primary_source()
    if fuente is None:
        print("\nLa base de datos está vacía.\n")
        return 1

    end = datetime.now(timezone.utc) + timedelta(days=1)
    start = end - timedelta(days=365 * 10)

    print("\n  Generando informe...")

    inventario = pd.DataFrame(repo.inventory())
    inventario["first_ts"] = pd.to_datetime(inventario["first_ts"], format="mixed", utc=True)
    inventario["last_ts"] = pd.to_datetime(inventario["last_ts"], format="mixed", utc=True)

    señales, resultados, graficos = [], [], []

    for entry in active_catalog():
        bars = repo.get_bars(entry.symbol, Timeframe.D1, fuente, start, end)
        if not bars:
            continue
        frame = bars_to_frame(bars)
        inst = entry.instrument
        fin = inst.financing if isinstance(inst, CFD) else None

        for estrategia in build_registry().values():
            if len(frame) < estrategia.warmup_bars + 10:
                continue
            s = estrategia.explain(frame, entry.symbol)
            if s:
                fila = s.to_row()
                fila["categoria"] = entry.group
                señales.append(fila)
            try:
                resultados.append(
                    run_backtest(frame, estrategia, entry.symbol, financing=fin).summary_row
                )
            except ValueError:
                pass

        if entry.symbol in ("US500", "XAUUSD", "EURUSD", "USOIL"):
            graficos.append(
                grafico_precio(frame["close"], f"{entry.symbol} — {inst.name}")
            )
        print(f"    {entry.symbol} procesado")

    # ---------------- Simulación de cartera ---------------- #
    print("    simulando cartera...")
    precios_sim, instrumentos_sim = {}, {}
    for entry in active_catalog():
        bars = repo.get_bars(entry.symbol, Timeframe.D1, fuente, start, end)
        if len(bars) < 300:
            continue
        precios_sim[entry.symbol] = bars_to_frame(bars)
        instrumentos_sim[entry.symbol] = entry.instrument

    from decimal import Decimal as D

    CAPITAL = 100_000
    sim = simulate_portfolio(
        precios_sim, instrumentos_sim, build_registry(),
        initial_capital=D(str(CAPITAL)), risk_config=RiskConfig(),
    )

    df_senales = pd.DataFrame(señales)
    df_bt = pd.DataFrame(resultados)

    activas = df_senales[df_senales["direccion"] != "flat"] if not df_senales.empty else pd.DataFrame()
    compras = int((df_senales["direccion"] == "long").sum()) if not df_senales.empty else 0
    ventas = int((df_senales["direccion"] == "short").sum()) if not df_senales.empty else 0

    tabla_senales = pd.DataFrame()
    if not activas.empty:
        tabla_senales = activas[
            ["simbolo", "estrategia", "direccion", "conviccion", "precio", "motivo"]
        ].rename(
            columns={
                "simbolo": "Instrumento", "estrategia": "Estrategia",
                "direccion": "Señal", "conviccion": "Convicción",
                "precio": "Precio", "motivo": "Justificación",
            }
        )

    tabla_cobertura = inventario.copy()
    tabla_cobertura["Desde"] = tabla_cobertura["first_ts"].dt.date
    tabla_cobertura["Hasta"] = tabla_cobertura["last_ts"].dt.date
    tabla_cobertura = tabla_cobertura.rename(
        columns={"symbol": "Instrumento", "bars": "Barras", "source": "Fuente"}
    )[["Instrumento", "Fuente", "Barras", "Desde", "Hasta"]]

    filas_coste = []
    for e in catalog.CATALOG:
        f = getattr(e.instrument, "financing", None)
        if f is None:
            continue
        filas_coste.append(
            {
                "Instrumento": e.symbol,
                "Nombre": e.instrument.name,
                "Coste anual comprado %": round(f.annual_pct(True), 2),
                "Ingreso anual vendido %": round(f.annual_pct(False), 2),
                "Origen": "Medido en el bróker" if f.measured else "Estimado",
            }
        )
    tabla_costes = pd.DataFrame(filas_coste)

    fecha = datetime.now().strftime("%d/%m/%Y")
    total_barras = repo.total_bars()
    n_instrumentos = len(inventario)
    anios = (inventario["last_ts"].max() - inventario["first_ts"].min()).days / 365.25

    # Bloque de cartera
    if sim.equity_curve.empty:
        bloque_cartera = "<p>Sin datos suficientes para reconstruir la cartera.</p>"
    else:
        m = sim.metrics
        filas_pos = [
            {
                "Instrumento": p.symbol,
                "Dirección": "COMPRA" if p.direction.value == "long" else "VENTA",
                "Unidades": round(float(p.units), 3),
                "Precio entrada": round(float(p.entry_price), 2),
                "Precio actual": round(float(p.current_price or 0), 2),
                "Invalidación": round(float(p.stop_price or 0), 2),
                "Resultado $": round(float(p.unrealized_pnl), 2),
                "Resultado %": round(float(p.return_pct), 2),
            }
            for p in sim.open_positions
        ]
        tabla_pos = (
            tabla_html(pd.DataFrame(filas_pos)) if filas_pos
            else "<p>Ninguna posición abierta en este momento.</p>"
        )
        ultimas = (
            tabla_html(pd.DataFrame([t.to_row() for t in sim.trades[-15:]][::-1]))
            if sim.trades else ""
        )
        bloque_cartera = f"""
<div class="kpis">
 <div class="kpi"><div class="v">${float(sim.final_equity):,.0f}</div><div class="l">Capital final</div></div>
 <div class="kpi"><div class="v">{sim.total_return_pct:+.1f}%</div><div class="l">Rentabilidad total</div></div>
 <div class="kpi"><div class="v">{m.get('cagr',0)*100:+.1f}%</div><div class="l">Rentabilidad anual</div></div>
 <div class="kpi"><div class="v">{m.get('max_drawdown',0)*100:.0f}%</div><div class="l">Caída máxima</div></div>
 <div class="kpi"><div class="v">{int(m.get('trades',0))}</div><div class="l">Operaciones</div></div>
</div>

<p>Reconstrucción de una cuenta de <b>${CAPITAL:,}</b> gestionada siguiendo las
señales del sistema, arriesgando un 1% del capital en cada operación y con un
máximo de 8 posiciones simultáneas. Incluye costes de financiación, diferencial
de compraventa y cierres automáticos por invalidación.</p>

{grafico_equity(sim.equity_curve, CAPITAL)}

<h3>Posiciones abiertas al cierre del periodo</h3>
{tabla_pos}

<h3>Comportamiento de las operaciones</h3>
<p>De {int(m.get('trades',0))} operaciones cerradas, resultaron ganadoras el
<b>{m.get('win_rate',0)*100:.0f}%</b>. La ganancia media fue de
${m.get('avg_win',0):,.0f} y la pérdida media de ${abs(m.get('avg_loss',0)):,.0f},
con una duración media de {m.get('avg_days_held',0):.0f} días por posición.</p>

<div class="nota">
<b>Cómo leer estas cifras.</b> Un porcentaje de aciertos bajo acompañado de
ganancias medias muy superiores a las pérdidas es el perfil característico del
seguimiento de tendencia: se pierde poco muchas veces y se gana mucho pocas
veces. Lo relevante no es acertar a menudo, sino que las operaciones acertadas
compensen ampliamente a las fallidas.
</div>

{('<h3>Últimas operaciones cerradas</h3>' + ultimas) if ultimas else ''}

<div class="aviso">
<b>Reconstrucción histórica, no resultados de operativa real.</b> Aplica las
reglas del sistema sobre datos de mercado reales. No incorpora deslizamiento, huecos de
apertura, rechazos de órdenes ni el efecto de la decisión humana. Los resultados
reales serían inferiores.
</div>
"""

    html = _plantilla(
        bloque_cartera=bloque_cartera,
        fecha=fecha,
        n_instrumentos=n_instrumentos,
        total_barras=total_barras,
        anios=anios,
        revisiones=repo.revision_count(),
        compras=compras,
        ventas=ventas,
        n_backtests=len(df_bt),
        tabla_senales=tabla_html(tabla_senales) if not tabla_senales.empty
            else "<p>No hay señales accionables en este momento.</p>",
        tabla_backtest=tabla_html(df_bt.sort_values("Sharpe", ascending=False))
            if not df_bt.empty else "",
        tabla_cobertura=tabla_html(tabla_cobertura),
        tabla_costes=tabla_html(tabla_costes),
        grafico_cobertura=grafico_cobertura(inventario),
        graficos_precio="\n".join(graficos),
    )

    salida = Path("informe_qq_quant_os.html")
    salida.write_text(html, encoding="utf-8")

    print()
    print(f"  Informe generado: {salida.resolve()}")
    print()
    print("  Es un archivo único. Se puede adjuntar a un correo y se abre")
    print("  en cualquier navegador sin instalar nada.")
    print()
    repo.close()
    return 0


def _plantilla(**k) -> str:
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QQ Quant OS — Informe de estado</title>
<style>
 body {{ font-family: -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        color:#1c2431; line-height:1.55; max-width:1080px; margin:0 auto;
        padding:32px 24px 80px; font-size:15px; }}
 header {{ border-top:5px solid #14304f; padding-top:26px; margin-bottom:38px; }}
 h1 {{ font-size:30px; color:#14304f; margin:0 0 6px; letter-spacing:-0.5px; }}
 .sub {{ color:#6b7787; font-size:15px; margin:0; }}
 .meta {{ color:#8a94a3; font-size:13px; margin-top:14px; }}
 h2 {{ font-size:19px; color:#14304f; margin:44px 0 8px;
      border-bottom:2px solid #14304f; padding-bottom:7px; }}
 h3 {{ font-size:15px; color:#2c3e56; margin:26px 0 6px; }}
 p {{ margin:0 0 12px; }}
 .kpis {{ display:flex; gap:14px; flex-wrap:wrap; margin:26px 0 8px; }}
 .kpi {{ flex:1; min-width:150px; background:#f4f7fa; border-top:3px solid #14304f;
         padding:16px 14px; text-align:center; }}
 .kpi .v {{ font-size:26px; font-weight:700; color:#14304f; line-height:1.1; }}
 .kpi .l {{ font-size:10.5px; color:#6b7787; text-transform:uppercase;
            letter-spacing:.9px; margin-top:6px; }}
 table.tabla {{ border-collapse:collapse; width:100%; font-size:12.5px; margin:14px 0 24px; }}
 table.tabla th {{ background:#14304f; color:#fff; text-align:left; padding:8px 9px;
                   font-size:11px; text-transform:uppercase; letter-spacing:.5px; }}
 table.tabla td {{ padding:7px 9px; border-bottom:1px solid #e4e9ef; vertical-align:top; }}
 table.tabla tr:nth-child(even) td {{ background:#f7f9fb; }}
 .nota {{ background:#f4f7fa; border-left:4px solid #14304f; padding:14px 16px; margin:18px 0; }}
 .aviso {{ background:#fdf6ee; border-left:4px solid #b8722c; padding:14px 16px; margin:18px 0; }}
 .aviso b, .nota b {{ color:#14304f; }}
 svg {{ max-width:100%; height:auto; margin:8px 0 20px; }}
 footer {{ margin-top:56px; padding-top:14px; border-top:1px solid #dfe4eb;
           color:#8a94a3; font-size:12px; }}
</style></head><body>

<header>
 <h1>QQ Quant OS</h1>
 <p class="sub">Plataforma de investigación cuantitativa multiactivo · Informe de estado</p>
 <p class="meta">Generado el {k['fecha']} · Sistema de sólo lectura: no envía órdenes al mercado</p>
</header>

<div class="kpis">
 <div class="kpi"><div class="v">{k['n_instrumentos']}</div><div class="l">Instrumentos</div></div>
 <div class="kpi"><div class="v">{k['total_barras']:,}</div><div class="l">Datos de precio</div></div>
 <div class="kpi"><div class="v">{k['anios']:.0f} años</div><div class="l">Histórico</div></div>
 <div class="kpi"><div class="v">{k['n_backtests']}</div><div class="l">Backtests</div></div>
 <div class="kpi"><div class="v">{k['revisiones']}</div><div class="l">Revisiones detectadas</div></div>
</div>

<h2>1. Qué hace el sistema</h2>
<p>QQ Quant OS descarga datos de mercado, calcula indicadores, ejecuta estrategias
cuantitativas y produce señales de inversión documentadas. <b>No envía órdenes</b>:
la decisión y la ejecución corresponden al operador. Esta restricción está
garantizada estructuralmente en el código y verificada de forma automática.</p>

<p>Actualmente hay {k['n_instrumentos']} instrumentos con {k['anios']:.0f} años de histórico
diario, sobre los que operan dos motores de estrategia. Cada señal incluye la
justificación de por qué se generó, lo que permite auditar cualquier decisión.</p>

<h2>2. Evolución de la cuenta</h2>
{k['bloque_cartera']}

<h2>3. Señales vigentes</h2>
<p>Situación actual: <b>{k['compras']} señales de compra</b> y <b>{k['ventas']} de venta</b>
entre todas las combinaciones de instrumento y estrategia.</p>
{k['tabla_senales']}

<div class="aviso">
<b>Sistema en validación.</b> Estas señales no han pasado por el control de calidad
de datos ni por la corrección estadística por selección múltiple. No deben usarse
para operar con capital real todavía.
</div>

<h2>4. Resultados históricos por estrategia</h2>
<p>Simulación sobre el histórico disponible, incorporando el coste de financiación
del bróker, el diferencial de compraventa y el desfase obligatorio entre el momento
en que se genera la señal y aquel en que se ejecuta.</p>
{k['tabla_backtest']}

<div class="aviso">
<b>Advertencia metodológica.</b> Estos resultados no están corregidos por selección
múltiple. Evaluar decenas de combinaciones garantiza encontrar algunas con buen
resultado por azar. Un ratio alto en esta tabla no es evidencia de que la estrategia
funcione: es un candidato que debe validarse fuera de muestra.
</div>

<h2>5. Coste de mantener posiciones</h2>
<p>Este es el hallazgo económico principal de la fase. El bróker cobra por mantener
posiciones compradas y <b>paga</b> por mantener posiciones vendidas. La asimetría es
estructural y condiciona qué estrategias tienen sentido en esta plataforma.</p>
{k['tabla_costes']}

<div class="nota">
<b>Implicación.</b> Cualquier estrategia que mantenga posiciones compradas durante
semanas debe superar entre un 9% y un 13% anual sólo para quedar en equilibrio.
Un análisis que ignore este coste sobrestima sistemáticamente la rentabilidad.
Sólo un instrumento tiene el coste medido directamente en el terminal; el resto
usa una estimación conservadora, pendiente de verificación.
</div>

<h2>6. Cobertura de datos</h2>
{k['grafico_cobertura']}
{k['tabla_cobertura']}

<h2>7. Series de precio</h2>
{k['graficos_precio']}

<h2>8. Estado del proyecto y siguientes pasos</h2>
<p>Se ha completado la cadena que va del dato a la señal: adquisición de datos,
almacenamiento trazable, cálculo de indicadores, estrategias, simulación histórica
con costes reales y presentación. Es la base sobre la que se construye el resto
de la plataforma.</p>

<h3>Pendiente a corto plazo</h3>
<ul>
<li>Control de calidad de datos: detección de huecos, valores atípicos y calendarios de sesión.</li>
<li>Medición de los costes de financiación de los 22 instrumentos restantes.</li>
<li>Validación fuera de muestra y corrección estadística por selección múltiple.</li>
<li>Conexión directa con el terminal del bróker para datos en tiempo real.</li>
</ul>

<h3>Decisiones que requieren dirección</h3>
<ul>
<li>Alojamiento del repositorio de código de la empresa.</li>
<li>Contratación de un proveedor de datos profesional cuando se pase a producción.</li>
</ul>

<footer>
QQ Quant OS · Documento generado automáticamente por el sistema ·
Confidencial, uso interno · El sistema no ejecuta operaciones
</footer>
</body></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
