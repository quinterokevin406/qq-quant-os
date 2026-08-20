"""Reconocimiento de intención sin coste: el asistente gratuito.

CÓMO FUNCIONA Y QUÉ NO ES
--------------------------
Esto NO es inteligencia artificial. Es un reconocedor de intenciones por
palabras clave: compara lo que escribes con un catálogo de preguntas conocidas
y, cuando encuentra una que encaja, ejecuta código que lee los datos reales del
sistema.

Consecuencias, buenas y malas:

**A favor.** Coste cero. Respuesta instantánea. Y no puede inventarse nada,
porque no genera texto libre: sólo rellena plantillas con cifras que vienen de
la base de datos.

**En contra.** Sólo entiende lo que está en este catálogo. Si preguntas algo
con palabras que no reconoce, lo dice y te ofrece lo que sí sabe hacer, en
lugar de improvisar una respuesta.

Añadir más patrones no "entrena" nada: amplía el catálogo de formas de
preguntar reconocidas.

CÓMO PUNTÚA
-----------
Cada intención declara palabras obligatorias (al menos una debe aparecer) y
palabras que refuerzan. La puntuación es la suma de coincidencias, ponderada
por lo específica que sea cada palabra. Gana la intención con más puntos, y si
ninguna llega al umbral se responde con la ayuda.

Las tildes y mayúsculas se ignoran, porque nadie las escribe de forma
consistente al preguntar deprisa.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

UMBRAL = 1.0
"""Puntuación mínima para dar una intención por reconocida."""


class Intent(StrEnum):
    """Tipos de pregunta que el asistente reconoce sin coste."""

    SALUDO = "saludo"
    AYUDA = "ayuda"

    # Señales
    SENALES_FUERTES = "senales_fuertes"
    SENALES_COMPRA = "senales_compra"
    SENALES_VENTA = "senales_venta"
    SENALES_CUANTAS = "senales_cuantas"
    SENALES_INSTRUMENTO = "senales_instrumento"
    SENALES_ESTRATEGIA = "senales_estrategia"
    RESUMEN_DIA = "resumen_dia"

    # Seguimiento
    SEGUIMIENTO_ESTADO = "seguimiento_estado"
    SEGUIMIENTO_CAMBIOS = "seguimiento_cambios"
    SEGUIMIENTO_MEJOR = "seguimiento_mejor"
    SEGUIMIENTO_ALERTA = "seguimiento_alerta"

    # Capital y riesgo
    REPARTO_CAPITAL = "reparto_capital"
    CUANTO_ARRIESGAR = "cuanto_arriesgar"
    ESTADO_CUENTA = "estado_cuenta"
    RIESGO_ABIERTO = "riesgo_abierto"
    DIVERSIFICACION = "diversificacion"
    CORRELACION = "correlacion"

    # Catálogo y sistema
    LISTA_INSTRUMENTOS = "lista_instrumentos"
    LISTA_ESTRATEGIAS = "lista_estrategias"
    EXPLICAR_ESTRATEGIA = "explicar_estrategia"
    COSTE_FINANCIACION = "coste_financiacion"
    CALIDAD_DATOS = "calidad_datos"
    ACTUALIZACION_DATOS = "actualizacion_datos"
    CUANTOS_DATOS = "cuantos_datos"

    # Rendimiento histórico
    MEJOR_ESTRATEGIA = "mejor_estrategia"
    MEJOR_INSTRUMENTO = "mejor_instrumento"
    RENDIMIENTO_HISTORICO = "rendimiento_historico"

    # Conceptos
    GLOSARIO = "glosario"
    POR_QUE_NO_OPERAR = "por_que_no_operar"
    QUE_ES_SISTEMA = "que_es_sistema"
    EJECUTA_ORDENES = "ejecuta_ordenes"

    DESCONOCIDA = "desconocida"


@dataclass(frozen=True)
class IntentPattern:
    """Patrón de reconocimiento de una intención.

    Attributes:
        intent: Intención que representa.
        required: Al menos una de estas palabras debe aparecer.
        boost: Palabras que refuerzan la puntuación.
        weight: Peso de las palabras obligatorias. Sube para intenciones muy
            específicas que deben ganar a otras más genéricas.
    """

    intent: Intent
    required: tuple[str, ...]
    boost: tuple[str, ...] = ()
    weight: float = 1.0


@dataclass
class Match:
    """Resultado del reconocimiento.

    Attributes:
        intent: Intención reconocida.
        score: Puntuación obtenida.
        symbol: Instrumento mencionado, si lo hay.
        strategy: Estrategia mencionada, si la hay.
        amount: Cantidad monetaria mencionada, si la hay.
        term: Término del que se pide definición, si aplica.
        alternatives: Otras intenciones que también encajaban.
    """

    intent: Intent
    score: float = 0.0
    symbol: str | None = None
    strategy: str | None = None
    amount: float | None = None
    term: str | None = None
    alternatives: list[Intent] = field(default_factory=list)


def normalizar(texto: str) -> str:
    """Pasa a minúsculas, quita tildes y signos.

    Nadie escribe con tildes consistentes cuando pregunta deprisa, así que
    compararlas sería una fuente segura de falsos negativos.
    """
    t = unicodedata.normalize("NFD", texto.lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    return re.sub(r"[^\w\s]", " ", t)


PATRONES: tuple[IntentPattern, ...] = (
    # --- Cortesía --------------------------------------------------------- #
    IntentPattern(
        Intent.SALUDO,
        ("hola", "buenas", "buenos dias", "buenas tardes", "hey", "que tal",
         "saludos", "hello"),
        weight=1.2,
    ),
    IntentPattern(
        Intent.AYUDA,
        ("ayuda", "que puedes", "que sabes", "para que sirves", "opciones", "comandos"),
        ("hacer", "preguntar", "responder"),
        weight=1.5,
    ),

    # --- Señales ---------------------------------------------------------- #
    IntentPattern(
        Intent.SENALES_FUERTES,
        ("fuerte", "fuertes", "strong", "mejores"),
        ("senal", "senales", "operacion", "operaciones", "hoy", "convicicion"),
        weight=1.5,
    ),
    IntentPattern(
        Intent.SENALES_COMPRA,
        ("compra", "compras", "comprar", "largo", "largos", "long", "longs",
         "alcista", "alcistas"),
        ("senal", "senales", "operacion", "operaciones", "hoy", "dame", "hay"),
        weight=1.4,
    ),
    IntentPattern(
        Intent.SENALES_VENTA,
        ("venta", "ventas", "vender", "corto", "cortos", "short", "shorts",
         "bajista", "bajistas"),
        ("senal", "senales", "operacion", "operaciones", "hoy", "dame", "hay"),
        weight=1.4,
    ),
    IntentPattern(
        Intent.SENALES_CUANTAS,
        ("cuantas", "cuantos", "numero de", "total de"),
        ("senal", "senales", "operaciones", "hay", "hoy"),
        weight=1.3,
    ),
    IntentPattern(
        Intent.SENALES_INSTRUMENTO,
        ("senal", "senales"),
        ("de", "sobre", "para", "en", "hay"),
        weight=0.9,
    ),
    IntentPattern(
        Intent.RESUMEN_DIA,
        ("resumen", "panorama", "como esta el mercado", "que hay hoy", "novedades"),
        ("hoy", "dia", "general", "todo"),
        weight=1.5,
    ),

    # --- Seguimiento ------------------------------------------------------ #
    IntentPattern(
        Intent.SEGUIMIENTO_ESTADO,
        ("seguimiento", "mis operaciones", "mis posiciones", "mis senales"),
        ("como", "van", "estado", "estan", "situacion"),
        weight=1.6,
    ),
    IntentPattern(
        Intent.SEGUIMIENTO_CAMBIOS,
        ("cambio", "cambiado", "cambios", "diferente", "modificado", "ayer"),
        ("seguimiento", "operacion", "operaciones", "senal", "horizonte", "plazo"),
        weight=1.6,
    ),
    IntentPattern(
        Intent.SEGUIMIENTO_MEJOR,
        ("probabilidad", "mas probable", "cual llega", "objetivo", "tp", "take profit"),
        ("mejor", "cual", "seguimiento", "alcanzar", "llegar"),
        weight=1.7,
    ),
    IntentPattern(
        Intent.SEGUIMIENTO_ALERTA,
        ("alerta", "atencion", "problema", "peligro", "cerca del stop", "invalidacion"),
        ("operacion", "seguimiento", "cuidado", "revisar"),
        weight=1.5,
    ),

    # --- Capital y riesgo ------------------------------------------------- #
    IntentPattern(
        Intent.REPARTO_CAPITAL,
        ("reparto", "reparte", "repartir", "asignar", "asigna", "distribuir",
         "cuanto capital", "cuanto a cada", "cuanto le pongo"),
        ("capital", "dinero", "operaciones", "riesgo", "entre"),
        weight=2.0,
    ),
    IntentPattern(
        Intent.CUANTO_ARRIESGAR,
        ("arriesgar", "riesgo de", "donde pongo", "donde meto", "invertir"),
        ("cuanto", "dolares", "capital", "dinero", "puedo"),
        weight=1.5,
    ),
    IntentPattern(
        Intent.ESTADO_CUENTA,
        ("equity", "balance", "cuenta", "capital actual", "cuanto tengo"),
        ("estado", "como", "mi", "total"),
        weight=1.5,
    ),
    IntentPattern(
        Intent.RIESGO_ABIERTO,
        ("riesgo abierto", "riesgo total", "cuanto riesgo", "expuesto", "exposicion"),
        ("tengo", "actual", "cartera", "portafolio"),
        weight=1.6,
    ),
    IntentPattern(
        Intent.DIVERSIFICACION,
        ("diversificado", "diversificacion", "diversificar", "concentrado", "concentracion"),
        ("estoy", "cartera", "bien", "riesgo"),
        weight=1.6,
    ),
    IntentPattern(
        Intent.CORRELACION,
        ("correlacion", "correlacionado", "correlacionados", "se mueven juntos", "parecidos"),
        ("instrumentos", "activos", "que", "cuales"),
        weight=1.6,
    ),

    # --- Catálogo --------------------------------------------------------- #
    IntentPattern(
        Intent.LISTA_INSTRUMENTOS,
        ("instrumentos", "activos", "mercados", "pares", "indices"),
        ("que", "cuales", "lista", "hay", "tienes", "disponibles"),
        weight=1.4,
    ),
    IntentPattern(
        Intent.LISTA_ESTRATEGIAS,
        ("estrategias",),
        ("que", "cuales", "lista", "hay", "tienes", "disponibles"),
        weight=1.4,
    ),
    IntentPattern(
        Intent.EXPLICAR_ESTRATEGIA,
        ("como funciona", "en que consiste", "explicame", "que hace"),
        ("estrategia", "tendencia", "reversion", "ruptura", "momento", "volatilidad"),
        weight=1.5,
    ),
    IntentPattern(
        Intent.COSTE_FINANCIACION,
        ("financiacion", "swap", "coste de mantener", "cuesta mantener", "overnight"),
        ("cuanto", "coste", "comprado", "vendido"),
        weight=1.7,
    ),
    IntentPattern(
        Intent.CALIDAD_DATOS,
        ("calidad", "datos fiables", "huecos", "problemas de datos"),
        ("datos", "series", "revisar"),
        weight=1.5,
    ),
    IntentPattern(
        Intent.ACTUALIZACION_DATOS,
        ("actualiza", "actualizan", "actualizacion", "ultimo dato", "cuando se"),
        ("datos", "precios", "sistema", "hora"),
        weight=1.4,
    ),
    IntentPattern(
        Intent.CUANTOS_DATOS,
        ("cuantas barras", "cuantos datos", "cuanto historico", "años de datos"),
        ("hay", "tienes", "almacenados"),
        weight=1.5,
    ),

    # --- Rendimiento ------------------------------------------------------ #
    IntentPattern(
        Intent.MEJOR_ESTRATEGIA,
        ("mejor estrategia", "que estrategia funciona", "estrategia mas rentable"),
        ("cual", "mejor", "historico", "rentable"),
        weight=1.8,
    ),
    IntentPattern(
        Intent.MEJOR_INSTRUMENTO,
        ("mejor instrumento", "mejor activo", "que instrumento funciona"),
        ("cual", "mejor", "historico", "rentable"),
        weight=1.8,
    ),
    IntentPattern(
        Intent.RENDIMIENTO_HISTORICO,
        ("rendimiento", "rentabilidad", "sharpe", "resultado historico", "backtest"),
        ("historico", "simulado", "cuanto", "ha dado"),
        weight=1.4,
    ),

    # --- Conceptos -------------------------------------------------------- #
    IntentPattern(
        Intent.GLOSARIO,
        ("que es", "que significa", "define", "definicion"),
        ("sharpe", "drawdown", "stop", "swap", "nocional", "expectativa", "riesgo"),
        weight=1.3,
    ),
    IntentPattern(
        Intent.POR_QUE_NO_OPERAR,
        ("por que no", "porque no", "no recomienda", "no hay nada", "no me da"),
        ("operar", "senales", "recomendaciones", "capital"),
        weight=1.6,
    ),
    IntentPattern(
        Intent.QUE_ES_SISTEMA,
        ("que es esto", "que es el sistema", "que es este sistema",
         "que hace el sistema", "para que sirve esto", "de que va"),
        ("sistema", "plataforma", "qq", "quant"),
        weight=2.2,
    ),
    IntentPattern(
        Intent.EJECUTA_ORDENES,
        ("ejecuta", "opera solo", "automatico", "abre operaciones", "manda ordenes"),
        ("sistema", "solo", "automaticamente", "broker"),
        weight=1.6,
    ),
)


TERMINOS_GLOSARIO = (
    "sharpe", "drawdown", "stop", "swap", "nocional", "expectativa",
    "correlacion", "volatilidad", "riesgo", "equity", "apalancamiento",
    "profit factor", "payoff", "atr", "var", "cvar", "backtest", "horizonte",
    "invalidacion", "objetivo", "convicicion", "regimen",
)


def _extraer_cantidad(texto: str) -> float | None:
    """Busca una cantidad monetaria en el texto.

    Reconoce '500', '1.500', '1,500', '2k', '10 mil'.
    """
    t = texto.replace(".", "").replace(",", "")
    m = re.search(r"\b(\d+)\s*(k|mil)\b", t)
    if m:
        return float(m.group(1)) * 1000
    m = re.search(r"\b(\d{3,9})\b", t)
    if m:
        return float(m.group(1))
    return None


def reconocer(
    pregunta: str,
    simbolos: tuple[str, ...] = (),
    estrategias: tuple[str, ...] = (),
) -> Match:
    """Reconoce la intención de una pregunta.

    Args:
        pregunta: Texto escrito por el operador.
        simbolos: Instrumentos del catálogo, para detectar menciones.
        estrategias: Nombres de estrategias, para detectar menciones.

    Returns:
        La intención reconocida, con las entidades encontradas. Si ninguna
        alcanza el umbral, devuelve `Intent.DESCONOCIDA`.
    """
    texto = normalizar(pregunta)
    palabras = set(texto.split())

    puntuaciones: list[tuple[float, Intent]] = []
    for pat in PATRONES:
        base = 0.0
        for palabra in pat.required:
            if " " in palabra:
                if palabra in texto:
                    base += pat.weight * 1.5
            elif palabra in palabras:
                base += pat.weight
        if base <= 0:
            continue
        for palabra in pat.boost:
            if (palabra in texto) if " " in palabra else (palabra in palabras):
                base += 0.35
        puntuaciones.append((base, pat.intent))

    puntuaciones.sort(key=lambda x: -x[0])

    # --- Entidades ---
    simbolo = None
    for s in simbolos:
        if normalizar(s) in palabras:
            simbolo = s
            break

    estrategia = None
    for e in estrategias:
        if normalizar(e) in texto:
            estrategia = e
            break

    termino = next((t for t in TERMINOS_GLOSARIO if t in texto), None)
    cantidad = _extraer_cantidad(texto)

    if not puntuaciones or puntuaciones[0][0] < UMBRAL:
        # Mencionar un instrumento sin más se interpreta como pedir sus señales.
        if simbolo:
            return Match(Intent.SENALES_INSTRUMENTO, 1.0, symbol=simbolo)
        return Match(Intent.DESCONOCIDA, 0.0, term=termino, amount=cantidad)

    mejor_puntuacion, mejor = puntuaciones[0]

    # Un instrumento concreto especializa la consulta de señales.
    if mejor in (Intent.SENALES_COMPRA, Intent.SENALES_VENTA, Intent.SENALES_FUERTES):
        pass  # se conserva la intención específica
    elif simbolo and mejor is Intent.SENALES_CUANTAS:
        mejor = Intent.SENALES_INSTRUMENTO

    return Match(
        intent=mejor,
        score=mejor_puntuacion,
        symbol=simbolo,
        strategy=estrategia,
        amount=cantidad,
        term=termino,
        alternatives=[i for _, i in puntuaciones[1:4]],
    )
