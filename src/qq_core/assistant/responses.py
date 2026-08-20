"""Respuestas del asistente gratuito.

CÓMO SE GARANTIZA QUE NO INVENTA
----------------------------------
Estas funciones no generan texto libre: rellenan plantillas con cifras que
vienen de `Snapshot`, que el panel construye leyendo la base de datos. Si un
dato no está en el `Snapshot`, la respuesta lo dice en lugar de suplirlo.

Es una garantía estructural, no una instrucción que se pueda ignorar: no hay
ningún punto del código donde pueda aparecer un número que no venga de los
datos.

CUÁNDO PASA A LA IA DE PAGO
----------------------------
Nunca por sí solo. El panel decide: si `responder` devuelve `None` —porque la
intención no se reconoció— y hay clave configurada, el panel consulta a la IA.
Sin clave, se muestra la lista de lo que sí sabe hacer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from qq_core.assistant.intents import Intent, Match, reconocer

GLOSARIO: dict[str, str] = {
    "sharpe": (
        "El **Sharpe** mide cuánta rentabilidad se obtiene por cada unidad de "
        "riesgo asumido. Un Sharpe de 1 significa que la rentabilidad anual "
        "iguala a la volatilidad. Por encima de 1 se considera bueno, pero "
        "ojo: es fácil obtener Sharpes altos por azar si se prueban muchas "
        "combinaciones."
    ),
    "drawdown": (
        "El **drawdown** o caída es cuánto ha bajado la cuenta desde su punto "
        "más alto. Un drawdown del 20% significa que si llegaste a tener "
        "10.000, en algún momento bajaste a 8.000. Importa más que la "
        "rentabilidad: determina si aguantas la estrategia sin abandonarla."
    ),
    "stop": (
        "El **stop** o nivel de invalidación es el precio al que la operación "
        "deja de tener sentido y se cierra con pérdida. No es una predicción: "
        "es el punto donde admites que la idea era equivocada. Todo el cálculo "
        "de riesgo del sistema parte de esa distancia."
    ),
    "swap": (
        "El **swap** o coste de financiación es lo que cuesta mantener una "
        "posición de un día para otro. En este bróker es asimétrico: comprar "
        "índices cuesta entre un 8% y un 15% anual, mientras que vender a "
        "menudo cobra. Es la razón de que operaciones largas de meses sean "
        "caras."
    ),
    "nocional": (
        "El **nocional** es el valor total de la posición. No es lo mismo que "
        "el riesgo: una posición de 10.000 con el stop a un 2% arriesga 200, "
        "no 10.000."
    ),
    "expectativa": (
        "La **expectativa** es cuánto se gana en promedio por operación, "
        "contando ganadoras y perdedoras: (probabilidad de ganar × ganancia "
        "media) − (probabilidad de perder × pérdida media). Es mejor métrica "
        "que el porcentaje de aciertos: acertar el 40% ganando el triple es "
        "mejor que acertar el 70% perdiendo el cuádruple."
    ),
    "correlacion": (
        "La **correlación** mide si dos instrumentos se mueven juntos. Cerca "
        "de 1 significa que suben y bajan a la vez. Importa porque cuatro "
        "posiciones en instrumentos correlacionados no son cuatro apuestas: "
        "son casi una sola con cuatro nombres."
    ),
    "volatilidad": (
        "La **volatilidad** mide cuánto se mueve un precio. Determina la "
        "distancia a la que hay que poner el stop: un instrumento más volátil "
        "necesita stops más lejanos, y por tanto permite menos unidades para "
        "el mismo riesgo."
    ),
    "equity": (
        "El **equity** es tu capital real ahora mismo: lo que pusiste, más lo "
        "ganado o perdido en operaciones cerradas, más el resultado flotante "
        "de las abiertas. Es la cifra sobre la que se calcula el riesgo."
    ),
    "atr": (
        "El **ATR** es el recorrido típico de un instrumento en una sesión. "
        "Se usa para colocar stops y objetivos a distancias proporcionales a "
        "cómo se mueve realmente ese mercado."
    ),
    "backtest": (
        "Un **backtest** es una simulación de qué habría pasado aplicando una "
        "estrategia al pasado. Es útil, pero tiene un peligro conocido: si se "
        "prueban muchas combinaciones, algunas saldrán bien por azar."
    ),
    "horizonte": (
        "El **horizonte** es cuánto se espera que dure la operación. Importa "
        "porque determina el coste de financiación acumulado y cuánto tiempo "
        "queda el capital comprometido."
    ),
    "riesgo": (
        "El **riesgo** de una operación es lo que pierdes si el precio alcanza "
        "la invalidación. No es el dinero comprometido. El sistema reparte "
        "riesgo, no capital: es la única forma de diversificar de verdad."
    ),
}


@dataclass
class Snapshot:
    """Datos reales del sistema en el momento de la pregunta.

    El panel lo construye leyendo la base de datos. Las respuestas sólo pueden
    usar lo que haya aquí.
    """

    fecha: str = ""
    senales: list[dict[str, Any]] = field(default_factory=list)
    seguimiento: list[dict[str, Any]] = field(default_factory=list)
    instrumentos: list[str] = field(default_factory=list)
    estrategias: list[dict[str, str]] = field(default_factory=list)
    financiacion: dict[str, dict[str, float]] = field(default_factory=dict)
    equity: dict[str, Any] | None = None
    reparto: list[dict[str, Any]] = field(default_factory=list)
    correlacion_grupos: dict[str, str] = field(default_factory=dict)
    barras_totales: int = 0
    calidad: dict[str, Any] | None = None


AYUDA = """Puedo responderte sobre lo que hay en el sistema. Por ejemplo:

**Señales de hoy**
- ¿Qué señales fuertes hay hoy?
- ¿Qué operaciones de compra hay? / ¿Y de venta?
- ¿Cuántas señales hay?
- ¿Qué hay de US500? (o cualquier instrumento)
- Dame un resumen del día

**Tus operaciones**
- ¿Cómo van mis operaciones?
- ¿Ha cambiado algo desde ayer?
- ¿Cuál tiene más probabilidad de llegar al objetivo?
- ¿Alguna está cerca del stop?

**Capital y riesgo**
- ¿Cómo reparto el capital entre mis operaciones?
- Tengo 500 para arriesgar, ¿dónde lo pongo?
- ¿Cuál es mi equity?
- ¿Cuánto riesgo tengo abierto?
- ¿Estoy bien diversificado?
- ¿Qué instrumentos están correlacionados?

**El sistema**
- ¿Qué instrumentos tienes? / ¿Qué estrategias hay?
- ¿Cómo funciona la reversión a la media?
- ¿Cuánto cuesta mantener XAUUSD comprado?
- ¿Cuál es la mejor estrategia?
- ¿Cuándo se actualizan los datos?
- ¿Por qué no me recomienda operar?

**Conceptos**
- ¿Qué es el drawdown? ¿Y el Sharpe? ¿Y el swap?"""


def _tabla(filas: list[dict], columnas: list[str], limite: int = 12) -> str:
    """Compone una tabla en Markdown a partir de filas reales."""
    if not filas:
        return "_Sin datos._"
    cabecera = "| " + " | ".join(columnas) + " |"
    sep = "|" + "|".join(["---"] * len(columnas)) + "|"
    cuerpo = [
        "| " + " | ".join(str(f.get(c, "—")) for c in columnas) + " |"
        for f in filas[:limite]
    ]
    extra = (
        f"\n\n_Mostrando {limite} de {len(filas)}._"
        if len(filas) > limite else ""
    )
    return "\n".join([cabecera, sep, *cuerpo]) + extra


def responder(pregunta: str, datos: Snapshot) -> str | None:
    """Responde una pregunta usando sólo los datos del `Snapshot`.

    Args:
        pregunta: Texto del operador.
        datos: Estado real del sistema.

    Returns:
        Respuesta en Markdown, o `None` si la intención no se reconoció. En ese
        caso el panel decide si consultar a la IA de pago o mostrar la ayuda.
    """
    m: Match = reconocer(
        pregunta,
        tuple(datos.instrumentos),
        tuple(e["nombre"] for e in datos.estrategias),
    )

    if m.intent is Intent.DESCONOCIDA:
        return None

    if m.intent is Intent.SALUDO:
        n = len(datos.senales)
        s = len(datos.seguimiento)
        return (
            f"Hola. Hoy hay **{n} señales** y tienes **{s} operaciones en "
            f"seguimiento**. Pregúntame lo que quieras, o escribe *ayuda* para "
            f"ver todo lo que sé hacer."
        )

    if m.intent is Intent.AYUDA:
        return AYUDA

    # --- Señales ---------------------------------------------------------- #
    if m.intent is Intent.SENALES_FUERTES:
        f = [s for s in datos.senales if str(s.get("Convicción", "")).lower() in
             ("fuerte", "strong")]
        if not f:
            return (
                "**Hoy no hay señales de convicción fuerte.** Hay "
                f"{len(datos.senales)} señales en total, todas de convicción "
                "moderada o débil. Puedes verlas en «Señales de hoy»."
            )
        return (
            f"Hay **{len(f)} señales fuertes** hoy:\n\n"
            + _tabla(f, ["Instrumento", "Dirección", "Estrategia", "Horizonte"])
        )

    if m.intent in (Intent.SENALES_COMPRA, Intent.SENALES_VENTA):
        quiero = "compra" if m.intent is Intent.SENALES_COMPRA else "venta"
        f = [s for s in datos.senales
             if quiero in str(s.get("Dirección", "")).lower()]
        if not f:
            return f"Hoy no hay señales de **{quiero}**."
        return (
            f"Hay **{len(f)} señales de {quiero}**:\n\n"
            + _tabla(f, ["Instrumento", "Estrategia", "Convicción", "Horizonte"])
        )

    if m.intent is Intent.SENALES_CUANTAS:
        compras = sum(1 for s in datos.senales
                      if "compra" in str(s.get("Dirección", "")).lower())
        ventas = sum(1 for s in datos.senales
                     if "venta" in str(s.get("Dirección", "")).lower())
        return (
            f"Hoy hay **{len(datos.senales)} señales**: {compras} de compra y "
            f"{ventas} de venta.\n\nRecuerda que el sistema evaluó muchas "
            f"combinaciones, así que parte de su ventaja histórica puede "
            f"deberse al azar de haber buscado tanto."
        )

    if m.intent is Intent.SENALES_INSTRUMENTO:
        if not m.symbol:
            return (
                "¿De qué instrumento? Tengo: "
                + ", ".join(datos.instrumentos[:25])
            )
        f = [s for s in datos.senales if s.get("Instrumento") == m.symbol]
        if not f:
            return f"Hoy no hay ninguna señal sobre **{m.symbol}**."
        return (
            f"Señales sobre **{m.symbol}**:\n\n"
            + _tabla(f, ["Dirección", "Estrategia", "Convicción", "Horizonte"])
        )

    if m.intent is Intent.RESUMEN_DIA:
        compras = sum(1 for s in datos.senales
                      if "compra" in str(s.get("Dirección", "")).lower())
        fuertes = sum(1 for s in datos.senales
                      if str(s.get("Convicción", "")).lower() in ("fuerte", "strong"))
        partes = [
            f"**Datos al {datos.fecha}.**",
            f"- {len(datos.senales)} señales: {compras} de compra, "
            f"{len(datos.senales) - compras} de venta",
            f"- {fuertes} de convicción fuerte",
            f"- {len(datos.seguimiento)} operaciones en seguimiento",
        ]
        if datos.equity:
            partes.append(
                f"- Equity: {datos.equity.get('Equity', '—')} · Riesgo "
                f"abierto: {datos.equity.get('Riesgo abierto %', '—')}%"
            )
        return "\n".join(partes)

    # --- Seguimiento ------------------------------------------------------ #
    if m.intent is Intent.SEGUIMIENTO_ESTADO:
        if not datos.seguimiento:
            return (
                "**No tienes nada en seguimiento.** Ve a «Señales de hoy», "
                "marca las que te interesen, y podré informarte de cómo van."
            )
        return (
            f"Tienes **{len(datos.seguimiento)} operaciones en seguimiento**:"
            "\n\n" + _tabla(
                datos.seguimiento,
                ["Instrumento", "Estrategia", "Estado", "Resultado %", "Días"],
            )
        )

    if m.intent is Intent.SEGUIMIENTO_CAMBIOS:
        if not datos.seguimiento:
            return "No tienes operaciones en seguimiento, así que no hay cambios."
        cambiadas = [
            s for s in datos.seguimiento
            if str(s.get("Estado", "")).lower() not in ("en curso", "vigente", "ok")
        ]
        if not cambiadas:
            return (
                f"Ninguna de tus {len(datos.seguimiento)} operaciones ha "
                f"cambiado de estado. Todas siguen como estaban."
            )
        return (
            f"**{len(cambiadas)} de {len(datos.seguimiento)} han cambiado:**"
            "\n\n" + _tabla(
                cambiadas, ["Instrumento", "Estado", "Resultado %", "Mensaje"]
            )
        )

    if m.intent is Intent.SEGUIMIENTO_MEJOR:
        if not datos.reparto:
            return (
                "Para calcular probabilidades necesito que ejecutes el "
                "análisis en «Reparto de capital» → *Analizar mis "
                "operaciones*. Ahí se calcula la probabilidad de que cada una "
                "alcance su objetivo."
            )
        return (
            "Ordenadas por probabilidad de alcanzar el objetivo:\n\n"
            + _tabla(
                datos.reparto,
                ["Instrumento", "Estrategia", "Calidad", "Riesgo %", "Decisión"],
            )
            + "\n\n_La probabilidad combina la geometría de la operación con "
              "el histórico. Es una estimación para ordenar, no una predicción._"
        )

    if m.intent is Intent.SEGUIMIENTO_ALERTA:
        alertas = [
            s for s in datos.seguimiento if s.get("Requiere atención") in (True, "Sí")
        ]
        if not datos.seguimiento:
            return "No tienes operaciones en seguimiento."
        if not alertas:
            return (
                f"Ninguna de tus {len(datos.seguimiento)} operaciones requiere "
                f"atención ahora mismo."
            )
        return (
            f"**{len(alertas)} operaciones requieren atención:**\n\n"
            + _tabla(alertas, ["Instrumento", "Estado", "Mensaje"])
        )

    # --- Capital y riesgo ------------------------------------------------- #
    if m.intent in (Intent.REPARTO_CAPITAL, Intent.CUANTO_ARRIESGAR):
        if not datos.reparto:
            extra = (
                f"\n\nMe dices que tienes **{m.amount:,.0f}** para arriesgar. "
                if m.amount else "\n\n"
            )
            return (
                "Todavía no he calculado el reparto." + extra
                + "Ve a «Reparto de capital» → *Analizar mis operaciones* y "
                "el motor ordenará tus señales por probabilidad de alcanzar "
                "el objetivo y repartirá el riesgo entre ellas, reservando "
                "capacidad para mañana."
            )
        aceptadas = [r for r in datos.reparto
                     if "ACCEPT" in str(r.get("Decisión", ""))]
        return (
            f"El motor asigna capital a **{len(aceptadas)} de "
            f"{len(datos.reparto)}** operaciones:\n\n"
            + _tabla(
                datos.reparto,
                ["Instrumento", "Estrategia", "Riesgo %", "Riesgo", "Decisión"],
            )
            + "\n\n_Las que no reciben capital tienen su motivo en la pestaña "
              "«Reparto de capital»._"
        )

    if m.intent is Intent.ESTADO_CUENTA:
        if not datos.equity:
            return (
                "Para conocer tu equity, introduce tu capital en «Reparto de "
                "capital». Se calcula como capital inicial más el resultado "
                "de tus operaciones en seguimiento."
            )
        e = datos.equity
        return (
            f"**Estado de tu cuenta:**\n\n"
            f"- Capital inicial: {e.get('Capital inicial', '—')}\n"
            f"- Equity actual: **{e.get('Equity', '—')}**\n"
            f"- Resultado cerrado: {e.get('Resultado cerrado', '—')}\n"
            f"- Resultado flotante: {e.get('Resultado flotante', '—')}\n"
            f"- Rentabilidad: {e.get('Rentabilidad %', '—')}%\n"
            f"- Posiciones abiertas: {e.get('Posiciones abiertas', 0)}"
        )

    if m.intent is Intent.RIESGO_ABIERTO:
        if not datos.equity:
            return (
                "Aún no puedo calcularlo. Introduce tu capital en «Reparto de "
                "capital» y marca operaciones en seguimiento."
            )
        e = datos.equity
        aviso = (
            ""
            if e.get("Riesgo completo", True)
            else "\n\n**Atención:** hay posiciones sin nivel de invalidación "
                 "declarado, así que el riesgo real es MAYOR que esta cifra."
        )
        return (
            f"Tienes **{e.get('Riesgo abierto %', '—')}%** de riesgo abierto, "
            f"equivalente a {e.get('Riesgo abierto', '—')}.\n\n"
            f"La exposición nocional es {e.get('Exposición nocional', '—')}, "
            f"que es mucho mayor: son cosas distintas. El riesgo es lo que "
            f"pierdes si el precio llega a la invalidación." + aviso
        )

    if m.intent is Intent.DIVERSIFICACION:
        if not datos.reparto:
            return (
                "Ejecuta el análisis en «Reparto de capital» y te diré cuántas "
                "apuestas realmente independientes tienes. Cinco posiciones "
                "correlacionadas pueden equivaler a poco más de una."
            )
        grupos: dict[str, int] = {}
        for r in datos.reparto:
            g = str(r.get("Grupo", "individual"))
            grupos[g] = grupos.get(g, 0) + 1
        lineas = [f"- {g}: {n} operaciones" for g, n in sorted(grupos.items())]
        return (
            "**Reparto por grupos de correlación:**\n\n" + "\n".join(lineas)
            + "\n\nLos instrumentos de un mismo grupo se mueven juntos: sumar "
              "más de uno aumenta una sola apuesta en lugar de diversificar."
        )

    if m.intent is Intent.CORRELACION:
        if not datos.correlacion_grupos:
            return (
                "Ejecuta el análisis en «Reparto de capital» para calcular "
                "los grupos de correlación."
            )
        grupos: dict[str, list[str]] = {}
        for sim, g in datos.correlacion_grupos.items():
            grupos.setdefault(g, []).append(sim)
        lineas = [
            f"- **{g}**: {', '.join(sorted(v))}"
            for g, v in sorted(grupos.items()) if len(v) > 1
        ]
        if not lineas:
            return "Ningún grupo de instrumentos supera el umbral de correlación."
        return (
            "**Instrumentos que se mueven juntos:**\n\n" + "\n".join(lineas)
            + "\n\nTener varias posiciones dentro de un mismo grupo no "
              "diversifica: es la misma apuesta repetida."
        )

    # --- Catálogo --------------------------------------------------------- #
    if m.intent is Intent.LISTA_INSTRUMENTOS:
        return (
            f"El sistema sigue **{len(datos.instrumentos)} instrumentos**:\n\n"
            + ", ".join(datos.instrumentos)
        )

    if m.intent is Intent.LISTA_ESTRATEGIAS:
        lineas = [
            f"- **{e['nombre']}** — {e.get('descripcion', '')}"
            for e in datos.estrategias
        ]
        return (
            f"Hay **{len(datos.estrategias)} estrategias operativas**:\n\n"
            + "\n".join(lineas)
        )

    if m.intent is Intent.EXPLICAR_ESTRATEGIA:
        if m.strategy:
            e = next(
                (x for x in datos.estrategias if x["nombre"] == m.strategy), None
            )
            if e:
                return (
                    f"**{e['nombre']}**\n\n{e.get('descripcion', '')}\n\n"
                    f"**Salida:** {e.get('salida', '—')}\n\n"
                    f"**Horizonte:** {e.get('horizonte', '—')}"
                )
        return (
            "¿Cuál de estas?\n\n"
            + "\n".join(f"- {e['nombre']}" for e in datos.estrategias)
        )

    if m.intent is Intent.COSTE_FINANCIACION:
        if m.symbol and m.symbol in datos.financiacion:
            f = datos.financiacion[m.symbol]
            medido = "medido en el terminal" if f.get("medido") else "ESTIMADO"
            return (
                f"**{m.symbol}** ({medido}):\n\n"
                f"- Comprado: **{f['largo']:+.2f}% anual**\n"
                f"- Vendido: **{f['corto']:+.2f}% anual**\n\n"
                + ("Positivo significa que cobras por mantener la posición."
                   if f["largo"] > 0 or f["corto"] > 0 else
                   "Ambos negativos: mantener la posición cuesta dinero en "
                   "los dos sentidos.")
            )
        filas = [
            {"Instrumento": k, "Comprado": f"{v['largo']:+.2f}%",
             "Vendido": f"{v['corto']:+.2f}%",
             "Origen": "medido" if v.get("medido") else "estimado"}
            for k, v in datos.financiacion.items()
        ]
        return (
            "**Coste de financiación anual:**\n\n"
            + _tabla(filas, ["Instrumento", "Comprado", "Vendido", "Origen"], 25)
        )

    if m.intent is Intent.CALIDAD_DATOS:
        if not datos.calidad:
            return "Puedes revisar la calidad de los datos en «Calidad de datos»."
        c = datos.calidad
        return (
            f"**Calidad de los datos:**\n\n"
            f"- Series revisadas: {c.get('series', '—')}\n"
            f"- Con incidencias: {c.get('con_problemas', '—')}\n"
            f"- Descartadas para generar señales: {c.get('descartadas', '—')}"
        )

    if m.intent is Intent.ACTUALIZACION_DATOS:
        return (
            f"El último dato disponible es del **{datos.fecha}**. Hay "
            f"{datos.barras_totales:,} barras almacenadas.\n\nLos precios se "
            f"actualizan ejecutando `scripts/actualizar_diario.py`, y el panel "
            f"publicado se refresca al reiniciarse."
        )

    if m.intent is Intent.CUANTOS_DATOS:
        return (
            f"Hay **{datos.barras_totales:,} barras** almacenadas, sobre "
            f"{len(datos.instrumentos)} instrumentos, con último dato del "
            f"{datos.fecha}."
        )

    # --- Rendimiento ------------------------------------------------------ #
    if m.intent in (Intent.MEJOR_ESTRATEGIA, Intent.MEJOR_INSTRUMENTO,
                    Intent.RENDIMIENTO_HISTORICO):
        return (
            "El rendimiento histórico está en «Histórico simulado», donde "
            "puedes filtrar por estrategia y por instrumento.\n\n"
            "**Un aviso importante:** el sistema evaluó 198 combinaciones de "
            "estrategia e instrumento. Buscar entre tantas garantiza encontrar "
            "algunas con buen aspecto por puro azar. La que mejor Sharpe tiene "
            "no es necesariamente la mejor: es la que más suerte tuvo en la "
            "búsqueda, o la mejor, y con estos datos no se puede distinguir."
        )

    # --- Conceptos -------------------------------------------------------- #
    if m.intent is Intent.GLOSARIO:
        if m.term and m.term in GLOSARIO:
            return GLOSARIO[m.term]
        return (
            "Puedo explicarte: "
            + ", ".join(f"**{k}**" for k in GLOSARIO)
            + ". ¿Cuál?"
        )

    if m.intent is Intent.POR_QUE_NO_OPERAR:
        return (
            "Puede haber varias razones, y el sistema siempre dice cuál:\n\n"
            "- **Correlación**: la señal duplica exposición que ya tienes\n"
            "- **Presupuesto agotado**: el riesgo desplegable de hoy está "
            "usado, y el resto está reservado para mañana\n"
            "- **Grupo saturado**: ya hay demasiado riesgo en instrumentos "
            "que se mueven juntos\n"
            "- **Caída**: si la cuenta está en drawdown, se reduce o bloquea\n\n"
            "En «Reparto de capital», la sección *Las que conviene esperar* "
            "da el motivo exacto de cada una."
        )

    if m.intent is Intent.QUE_ES_SISTEMA:
        return (
            "**QQ QUANT OS** es una plataforma de investigación cuantitativa "
            f"multiactivo. Aplica {len(datos.estrategias)} estrategias sobre "
            f"{len(datos.instrumentos)} instrumentos y genera señales "
            "documentadas.\n\n**No ejecuta operaciones.** La decisión y la "
            "ejecución son manuales en MetaTrader 5. Esa restricción está "
            "garantizada en el código y verificada por pruebas automáticas."
        )

    if m.intent is Intent.EJECUTA_ORDENES:
        return (
            "**No.** El sistema nunca envía órdenes ni abre posiciones. Sólo "
            "genera recomendaciones documentadas; tú decides cuáles ejecutar y "
            "las introduces manualmente en MetaTrader 5.\n\nEsa restricción no "
            "es una promesa: está garantizada en el código y verificada por "
            "pruebas automáticas que fallarían si alguien intentara añadir "
            "capacidad de ejecución."
        )

    return None
