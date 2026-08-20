"""Pruebas del asistente y de la probabilidad de alcanzar el objetivo.

La prueba más importante es `test_el_asistente_no_puede_inventar_datos`: el
asistente recibe el estado ya cerrado y no tiene forma de consultar nada por su
cuenta. Un asistente conversacional que improvisa cifras es más peligroso que
una tabla equivocada, porque se lee como un consejo fundado.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from qq_core.assistant import Assistant, AssistantError, SystemContext
from qq_core.portfolio.target_probability import (
    Confidence,
    estimate_target_probability,
)


# --------------------------------------------------------------------------- #
# CA-108 a CA-112: probabilidad de alcanzar el objetivo
# --------------------------------------------------------------------------- #


def test_objetivo_lejano_es_menos_probable_que_uno_cercano() -> None:
    """CA-108: la geometría manda cuando no hay histórico.

    Si el objetivo está al doble de distancia que el stop, alcanzarlo antes es
    menos probable por pura aritmética, sin necesidad de estimar nada.
    """
    cercano = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("102"), "long"
    )
    lejano = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("110"), "long"
    )

    assert cercano is not None and lejano is not None
    assert cercano.probability > lejano.probability
    assert lejano.reward_risk > cercano.reward_risk


def test_una_operacion_avanzada_tiene_mas_probabilidad() -> None:
    """CA-109: se mide desde el precio ACTUAL, no desde la entrada.

    Una operación que ya recorrió parte del camino tiene el objetivo más cerca
    y la invalidación más lejos.
    """
    recien = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("106"), "long"
    )
    avanzada = estimate_target_probability(
        Decimal("100"), Decimal("104"), Decimal("98"), Decimal("106"), "long"
    )

    assert recien is not None and avanzada is not None
    assert avanzada.probability > recien.probability
    assert avanzada.progress_pct > recien.progress_pct


def test_el_historico_pesa_segun_el_tamano_de_muestra() -> None:
    """CA-110: con poca muestra manda la geometría, que no estima nada.

    Sin esto, una combinación con 5 operaciones afortunadas dominaría el orden.
    """
    poca = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("106"), "long",
        historical_hit_rate=0.9, n_trades=5,
    )
    mucha = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("106"), "long",
        historical_hit_rate=0.9, n_trades=500,
    )

    assert poca is not None and mucha is not None
    assert poca.weight_historical < 0.20
    assert mucha.weight_historical > 0.90
    assert mucha.probability > poca.probability


def test_la_confianza_declara_de_donde_sale_la_cifra() -> None:
    """CA-111: quien lea el número debe saber cuánto vale."""
    sin = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("106"), "long"
    )
    con = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), Decimal("106"), "long",
        historical_hit_rate=0.55, n_trades=200,
    )

    assert sin is not None and con is not None
    assert sin.confidence is Confidence.GEOMETRIC_ONLY
    assert con.confidence is Confidence.HIGH


def test_sin_objetivo_no_se_inventa_una_probabilidad() -> None:
    """CA-112: una operación sin objetivo no tiene probabilidad de alcanzarlo."""
    assert estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("98"), None, "long"
    ) is None


def test_valor_esperado_combina_probabilidad_y_recompensa() -> None:
    """CA-113: una probabilidad baja puede compensar si el premio es grande.

    Es el mismo principio que la expectativa: acertar poco y ganar mucho puede
    ser mejor que acertar mucho y ganar poco.
    """
    poco_probable_gran_premio = estimate_target_probability(
        Decimal("100"), Decimal("100"), Decimal("99"), Decimal("110"), "long"
    )
    assert poco_probable_gran_premio is not None
    assert poco_probable_gran_premio.probability < 0.20
    assert poco_probable_gran_premio.reward_risk > 5.0


# --------------------------------------------------------------------------- #
# CA-114 a CA-117: asistente
# --------------------------------------------------------------------------- #


def test_el_asistente_no_puede_inventar_datos() -> None:
    """CA-114: LA PRUEBA CRÍTICA del asistente.

    El estado se construye en código y se entrega cerrado. El asistente no
    tiene herramienta alguna para consultar por su cuenta ni para rellenar
    huecos: lo que no esté en el contexto, no existe para él.
    """
    recibido = {}

    def falso(payload):
        recibido.update(payload)
        return json.dumps({"content": [{"type": "text", "text": "ok"}]})

    a = Assistant(api_key="clave", fetcher=falso)
    ctx = SystemContext(cuenta="Equity 100.000", senales_hoy="US500 COMPRA")
    a.ask("¿qué hay hoy?", ctx)

    # No se declara ninguna herramienta: no puede llamar a nada.
    assert "tools" not in recibido

    enviado = recibido["messages"][-1]["content"]
    assert "ESTADO DEL SISTEMA" in enviado
    assert "US500 COMPRA" in enviado

    # Y las instrucciones prohíben inventar explícitamente.
    assert "ÚNICAMENTE" in recibido["system"]
    assert "inventas" in recibido["system"]


def test_sin_clave_falla_con_mensaje_util() -> None:
    """CA-115: un fallo de configuración se explica, no se traga."""
    a = Assistant(api_key="")
    assert not a.is_configured

    with pytest.raises(AssistantError) as exc:
        a.ask("hola", SystemContext())
    assert "ANTHROPIC_API_KEY" in str(exc.value)


def test_la_clave_vacia_no_cae_al_entorno(monkeypatch) -> None:
    """CA-116: el comportamiento no depende de cómo esté la máquina.

    Mismo defecto que se corrigió en el adaptador de Twelve Data: `None`
    significa "búscala en el entorno", una cadena vacía significa "sin clave".
    """
    monkeypatch.setenv("ANTHROPIC_API_KEY", "del_entorno")

    assert not Assistant(api_key="").is_configured
    assert Assistant(api_key=None).is_configured


def test_el_contexto_incluye_las_limitaciones() -> None:
    """CA-117: el asistente debe conocer las salvedades del sistema.

    Si no las conoce, presentará las señales con más seguridad de la que
    merecen. Y una frase amable convence más que una tabla.
    """
    ctx = SystemContext(
        senales_hoy="US500 COMPRA",
        avisos=["El sistema evaluó 198 combinaciones", "6 de 23 costes son estimados"],
    )
    texto = ctx.render()

    assert "LIMITACIONES" in texto
    assert "198 combinaciones" in texto


# --------------------------------------------------------------------------- #
# CA-118 a CA-122: gráfico de señal
# --------------------------------------------------------------------------- #


def _marco_precios(n: int = 400):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(3)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.01, n)))
    return pd.DataFrame(
        {"open": c, "high": c * 1.006, "low": c * 0.994, "close": c, "volume": 1e6},
        index=pd.date_range("2024-01-01", periods=n, freq="B"),
    )


def test_cada_estrategia_expone_sus_indicadores_reales() -> None:
    """CA-118: el gráfico dibuja lo que la estrategia usa, no una aproximación.

    Si se dibujaran medias genéricas, el operador creería que la señal viene de
    ahí. Es peor que no dibujar nada.
    """
    from qq_core.strategies.library import (
        MeanReversion, PullbackTrend, TrendFollowing, VolatilityBreakout,
    )

    marco = _marco_precios()

    tf = TrendFollowing().chart_overlays(marco)
    assert any("Media rápida" in k for k in tf)
    assert any("Media lenta" in k for k in tf)

    mr = MeanReversion().chart_overlays(marco)
    assert any("Banda" in k for k in mr)
    assert any(k.startswith("panel:") for k in mr)

    vb = VolatilityBreakout().chart_overlays(marco)
    assert any("Techo" in k for k in vb)
    assert any("Suelo" in k for k in vb)

    pb = PullbackTrend().chart_overlays(marco)
    assert any("tendencia" in k.lower() for k in pb)


def test_una_estrategia_sin_indicadores_devuelve_vacio() -> None:
    """CA-119: no se inventan indicadores para las que no los exponen."""
    from qq_core.strategies.library import Seasonality

    assert Seasonality().chart_overlays(_marco_precios()) == {}


def test_el_grafico_marca_los_tres_niveles() -> None:
    """CA-120: entrada, invalidación y objetivo deben verse."""
    from qq_core.charts import ChartLevels, build_signal_chart
    from qq_core.strategies.library import TrendFollowing

    marco = _marco_precios()
    estrategia = TrendFollowing()
    niveles = ChartLevels(
        entry=Decimal("100"), stop=Decimal("95"),
        target=Decimal("112"), direction="long",
    )

    fig = build_signal_chart(
        marco, estrategia.chart_overlays(marco), niveles, "US500", "Tendencia"
    )
    anotaciones = " ".join(
        str(a.get("text", "")) for a in fig.to_dict()["layout"].get("annotations", [])
    )

    assert "Entrada" in anotaciones
    assert "Invalidación" in anotaciones
    assert "Objetivo" in anotaciones


def test_los_indicadores_de_panel_van_en_una_fila_aparte() -> None:
    """CA-121: un z-score no puede dibujarse sobre la escala del precio.

    Un oscilador que oscila entre -3 y 3 sobre un precio de 5.000 sería una
    línea plana pegada al eje.
    """
    from qq_core.charts import ChartLevels, build_signal_chart
    from qq_core.strategies.library import MeanReversion

    marco = _marco_precios()
    fig = build_signal_chart(
        marco,
        MeanReversion().chart_overlays(marco),
        ChartLevels(Decimal("100"), Decimal("95"), Decimal("112"), "long"),
        "US500", "Reversión",
    )
    ejes = fig.to_dict()["layout"]

    assert "yaxis2" in ejes, "el oscilador debe ir en un panel propio"


def test_resumen_de_niveles_calcula_beneficio_riesgo() -> None:
    """CA-122: la relación beneficio/riesgo se muestra junto al gráfico."""
    from qq_core.charts import ChartLevels, levels_summary

    filas = levels_summary(
        ChartLevels(Decimal("100"), Decimal("95"), Decimal("115"), "long"),
        Decimal("100"),
    )
    br = next(f for f in filas if "Beneficio" in f["Nivel"])

    assert "3.00 a 1" in br["Distancia"]


# --------------------------------------------------------------------------- #
# CA-123 a CA-128: asistente gratuito
# --------------------------------------------------------------------------- #


def _snapshot():
    from qq_core.assistant import Snapshot

    return Snapshot(
        fecha="2026-08-20",
        senales=[
            {"Instrumento": "US500", "Dirección": "COMPRA",
             "Estrategia": "Tendencia", "Convicción": "fuerte",
             "Horizonte": "1 mes"},
            {"Instrumento": "EURUSD", "Dirección": "VENTA",
             "Estrategia": "Reversión", "Convicción": "moderada",
             "Horizonte": "1 semana"},
        ],
        seguimiento=[
            {"Instrumento": "US500", "Estrategia": "Tendencia",
             "Estado": "En curso", "Resultado %": 2.4, "Días": 6},
        ],
        instrumentos=["US500", "USTEC", "EURUSD", "XAUUSD"],
        estrategias=[
            {"nombre": "Reversión a la media", "descripcion": "Compra caídas",
             "salida": "al volver a la media", "horizonte": "2 semanas"},
        ],
        financiacion={"XAUUSD": {"largo": -5.23, "corto": 2.38, "medido": True}},
        barras_totales=57858,
    )


def test_el_asistente_gratuito_responde_con_datos_reales() -> None:
    """CA-123: las cifras salen del snapshot, no de una plantilla fija."""
    from qq_core.assistant import responder

    d = _snapshot()
    r = responder("cuantas barras hay almacenadas", d)

    assert r is not None
    assert "57,858" in r
    assert "2026-08-20" in r


def test_una_pregunta_fuera_del_sistema_no_se_responde() -> None:
    """CA-124: LA PRUEBA CRÍTICA del modo gratuito.

    Devolver `None` es lo correcto: el panel decide entonces si consultar a la
    IA de pago o mostrar la ayuda. Lo que nunca ocurre es que se improvise una
    respuesta.
    """
    from qq_core.assistant import responder

    d = _snapshot()
    assert responder("cual es la capital de francia", d) is None
    assert responder("cuentame un chiste", d) is None
    assert responder("va a subir el bitcoin manana", d) is None


def test_filtra_las_senales_de_verdad() -> None:
    """CA-125: no describe las señales, las filtra sobre los datos."""
    from qq_core.assistant import responder

    d = _snapshot()
    fuertes = responder("que señales strong hay hoy", d)
    ventas = responder("dame las ventas", d)

    assert fuertes is not None and "US500" in fuertes and "EURUSD" not in fuertes
    assert ventas is not None and "EURUSD" in ventas and "US500" not in ventas


def test_el_coste_declara_si_esta_medido_o_estimado() -> None:
    """CA-126: la procedencia del dato acompaña siempre a la cifra."""
    from qq_core.assistant import responder

    r = responder("cuanto cuesta mantener XAUUSD comprado", _snapshot())

    assert r is not None
    assert "-5.23" in r
    assert "medido" in r


def test_nunca_afirma_que_el_sistema_opera_solo() -> None:
    """CA-127: la restricción central del proyecto no se puede contradecir."""
    from qq_core.assistant import responder

    r = responder("el sistema ejecuta las ordenes solo", _snapshot())

    assert r is not None
    assert r.strip().startswith("**No.**")
    assert "manualmente" in r


def test_advierte_del_sesgo_al_preguntar_por_la_mejor_estrategia() -> None:
    """CA-128: la pregunta más peligrosa lleva su advertencia.

    "¿Cuál es la mejor?" invita a operar la que mejor backtest tuvo, que es
    precisamente la más afectada por el sesgo de haber buscado entre muchas.
    """
    from qq_core.assistant import responder

    r = responder("cual es la mejor estrategia", _snapshot())

    assert r is not None
    assert "198" in r
    assert "azar" in r
