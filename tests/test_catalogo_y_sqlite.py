"""Pruebas del catálogo, el modelo de financiación y el repositorio SQLite.

El repositorio SQLite ejecuta la MISMA batería de contrato que la
implementación en memoria. Es lo que garantiza que cambiar de motor de
persistencia no altere el comportamiento del sistema.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from qq_core.catalog import instruments as catalog
from qq_core.domain.bar import Bar
from qq_core.domain.enums import AssetClass, DataSource, Timeframe
from qq_core.domain.instrument import CFD, FinancingTerms, FxSpot
from qq_core.domain.provenance import Provenance, hash_payload
from qq_core.ingestion.service import utc
from qq_core.storage.sqlite_repository import SQLiteBarRepository


# --------------------------------------------------------------------- #
# Catálogo
# --------------------------------------------------------------------- #


def test_catalogo_tiene_universo_completo() -> None:
    """CA-18: el catálogo contiene los 23 instrumentos auditados."""
    assert len(catalog.CATALOG) == 23


def test_simbolos_canonicos_unicos() -> None:
    """Un símbolo duplicado mezclaría series de instrumentos distintos."""
    simbolos = [e.symbol for e in catalog.CATALOG]
    assert len(simbolos) == len(set(simbolos))


def test_todo_instrumento_tiene_ambas_identidades() -> None:
    """CA-19: separación investigación / ejecución.

    Cada instrumento declara dónde se ejecuta y de dónde salen sus datos de
    investigación. Son fuentes distintas a propósito.
    """
    for e in catalog.CATALOG:
        assert e.mt5_symbol, f"{e.symbol} sin símbolo de ejecución"
        assert e.stooq_symbol, f"{e.symbol} sin símbolo de investigación"


def test_los_cfd_declaran_emisor_gbe() -> None:
    """Un CFD sin emisor identificado no es trazable a sus condiciones."""
    for e in catalog.CATALOG:
        if isinstance(e.instrument, CFD):
            assert e.instrument.issuer == "GBE Global"


def test_ningun_instrumento_es_futuro() -> None:
    """CA-20: consecuencia directa de ADR-0003.

    El bróker no ofrece futuros con curva utilizable. Si algún día aparece un
    `Future` en el catálogo, este test falla y obliga a revisar el ADR.
    """
    for e in catalog.CATALOG:
        assert e.instrument.asset_class is not AssetClass.FUTURE


def test_pares_jpy_tienen_pip_correcto() -> None:
    """Caso límite clásico: USDJPY usa pip 0.01, no 0.0001.

    Confundirlos produce un error de dos órdenes de magnitud en el
    dimensionamiento de posición.
    """
    usdjpy = catalog.get("USDJPY").instrument
    eurusd = catalog.get("EURUSD").instrument
    assert isinstance(usdjpy, FxSpot) and isinstance(eurusd, FxSpot)
    assert usdjpy.pip_size == Decimal("0.01")
    assert eurusd.pip_size == Decimal("0.0001")


def test_get_falla_con_simbolo_desconocido() -> None:
    """Un símbolo mal escrito falla en el acto, no devuelve None."""
    with pytest.raises(KeyError):
        catalog.get("NOEXISTE")


def test_universo_principal_es_subconjunto() -> None:
    core = catalog.core_universe()
    assert 0 < len(core) < len(catalog.CATALOG)
    assert all(e in catalog.CATALOG for e in core)


# --------------------------------------------------------------------- #
# Modelo de financiación
# --------------------------------------------------------------------- #


def _terminos() -> FinancingTerms:
    """Términos medidos en US500.c de GBE (ver ADR-0003)."""
    return catalog.US500_FINANCING


def test_conversion_de_puntos_a_tasa_anual() -> None:
    """CA-21: los swaps en puntos se convierten a tasa anual comparable.

    Se verifica la CONVERSIÓN con valores fijos, no la medición vigente del
    catálogo. La versión anterior de esta prueba comprobaba que US500 valiera
    -13,4%, y falló al remedir el instrumento el 2026-08-19: había pasado a
    -8,63% porque los swaps siguen a los tipos de interés.

    Fijar en una prueba un valor que el mundo puede cambiar convierte una
    medición legítima en un fallo de CI. La prueba comprueba la aritmética;
    CA-21b comprueba que el catálogo declare correctamente su procedencia.
    """
    t = FinancingTerms.from_mt5_points("-183.075", "61.526", "0.01", "5000")

    # 183,075 puntos de 0,01 sobre 5.000 son un 0,0366% diario -> 13,4% anual.
    assert t.measured is True
    assert -0.14 < float(t.annual_rate_long) < -0.13
    assert 0.04 < float(t.annual_rate_short) < 0.05


def test_us500_declara_financiacion_medida() -> None:
    """CA-21b: el catálogo declara procedencia, no una cifra concreta.

    Lo que debe permanecer estable no es el valor —que caduca— sino que sea
    una medición real y no una estimación, y que la asimetría estructural del
    bróker se mantenga: comprado cuesta, vendido cobra.
    """
    t = _terminos()
    assert t.measured is True
    assert float(t.annual_rate_long) < 0 < float(t.annual_rate_short)


def test_las_divisas_tienen_financiacion_declarada() -> None:
    """CA-21c: `FxSpot` no puede quedarse sin coste de financiación.

    Hasta la v1.10, `financing_from_instrument` devolvía `None` para todo lo
    que no fuera CFD, así que los backtests de divisas se ejecutaban sin coste
    alguno. La medición mostró que el coste real va de -2,7% a +2,7% anual
    según el par y la dirección: tratarlo como cero era un error material.
    """
    from qq_core.backtest.engine import financing_from_instrument
    from qq_core.catalog.instruments import CATALOG

    sin_financiacion = [
        e.symbol
        for e in CATALOG
        if e.group == "Divisas" and financing_from_instrument(e.instrument) is None
    ]
    assert not sin_financiacion, (
        f"pares sin coste de financiación declarado: {sin_financiacion}"
    )


def test_tasa_no_depende_de_la_escala_del_precio() -> None:
    """El mismo coste económico sobre dos activos de precio muy distinto
    produce la misma tasa anual. Es lo que impide el error de copiar swaps
    entre instrumentos."""
    barato = FinancingTerms.from_mt5_points("-36.615", "0", "0.01", "1000")
    caro = FinancingTerms.from_mt5_points("-183.075", "0", "0.01", "5000")
    assert abs(float(barato.annual_rate_long) - float(caro.annual_rate_long)) < 1e-6


def test_coste_diario_comprado_es_negativo() -> None:
    """CA-22: mantener posición comprada cuesta dinero."""
    assert _terminos().daily_rate(is_long=True, weekday=0) < 0


def test_ingreso_diario_vendido_es_positivo() -> None:
    """CA-23: el bróker paga por mantener posición vendida.

    Es la asimetría estructural documentada en ADR-0003. Un backtest que la
    ignore subestima el rendimiento de las estrategias bajistas.
    """
    assert _terminos().daily_rate(is_long=False, weekday=0) > 0


def test_viernes_cobra_triple() -> None:
    """CA-24: el cargo del viernes cubre el fin de semana."""
    t = _terminos()
    assert t.daily_rate(True, weekday=4) == t.daily_rate(True, weekday=0) * 3


def test_coste_semanal_suma_cinco_dias_con_viernes_triple() -> None:
    t = _terminos()
    esperado = t.daily_rate(True, 0) * 4 + t.daily_rate(True, 4)
    assert t.weekly_rate(True) == esperado


def test_estimacion_se_marca_como_no_medida() -> None:
    """CA-25: distinguir dato medido de estimado no es opcional.

    Un backtest con costes estimados no tiene la misma validez que uno con
    costes reales, y quien lea el informe debe poder saberlo.
    """
    estimada = FinancingTerms.estimated()
    assert estimada.measured is False
    assert estimada.note


def test_alcance_de_la_medicion_esta_declarado() -> None:
    """CA-22: cada instrumento declara si su coste está medido o estimado.

    La versión anterior de esta prueba exigía que SÓLO US500 estuviera medido.
    Dejó de valer el 2026-08-19, cuando se midieron 17 de los 23 en el terminal
    con `scripts/medir_swaps.py`.

    Lo que debe seguir siendo cierto no es cuántos hay medidos, sino que la
    distinción entre medido y estimado sea explícita en cada uno. Un backtest
    sobre costes estimados no tiene la misma validez que uno sobre costes
    medidos, y quien lea el resultado tiene que poder saber cuál es cuál.
    """
    from qq_core.backtest.engine import financing_from_instrument

    medidos, estimados = [], []
    for e in catalog.CATALOG:
        t = financing_from_instrument(e.instrument)
        assert t is not None, f"{e.symbol} sin financiación declarada"
        (medidos if t.measured else estimados).append(e.symbol)

    # Los seis restantes no estaban visibles en el terminal al medir.
    assert len(medidos) >= 17
    assert set(estimados) <= {"US2000", "UK100", "F40", "SWI20", "HK50", "AUS200"}


def test_ningun_instrumento_finge_coste_cero() -> None:
    """CA-22b: un coste desconocido no puede presentarse como gratuito.

    Es el mismo principio que rige el puerto `AccountProvider`: un dato que no
    se tiene se declara, no se rellena con un cero. Antes de la v1.10 las
    divisas operaban sin coste alguno en el backtest, que es la versión
    silenciosa de este error.
    """
    from qq_core.backtest.engine import financing_from_instrument

    for e in catalog.CATALOG:
        t = financing_from_instrument(e.instrument)
        assert t is not None
        assert not (
            float(t.annual_rate_long) == 0.0
            and float(t.annual_rate_short) == 0.0
        ), f"{e.symbol} declara coste de financiación nulo en ambos sentidos"


def test_financing_applies_refleja_presencia_de_terminos() -> None:
    con = catalog.get("US500").instrument
    assert isinstance(con, CFD) and con.financing_applies is True


# --------------------------------------------------------------------- #
# Repositorio SQLite — mismo contrato que la implementación en memoria
# --------------------------------------------------------------------- #


@pytest.fixture
def repo() -> SQLiteBarRepository:
    return SQLiteBarRepository(":memory:")


def _prov() -> Provenance:
    return Provenance(
        source=DataSource.STOOQ,
        provider_symbol="^spx",
        ingested_at=datetime.now(timezone.utc),
        raw_payload_hash=hash_payload("payload"),
    )


def _bar(dia: int, cierre: str = "5000.00") -> Bar:
    return Bar(
        symbol="US500",
        timeframe=Timeframe.D1,
        ts=utc(2025, 1, dia),
        source=DataSource.STOOQ,
        open=Decimal("4990.00"),
        high=Decimal("5010.00"),
        low=Decimal("4980.00"),
        close=Decimal(cierre),
        volume=Decimal("1000"),
    )


def test_sqlite_escritura_y_lectura(repo: SQLiteBarRepository) -> None:
    report = repo.upsert_bars([_bar(2), _bar(3)], _prov())
    assert report.inserted == 2
    leidas = repo.get_bars(
        "US500", Timeframe.D1, DataSource.STOOQ, utc(2025, 1, 1), utc(2025, 2, 1)
    )
    assert len(leidas) == 2
    assert leidas[0].close == Decimal("5000.00")


def test_sqlite_reingesta_es_identica(repo: SQLiteBarRepository) -> None:
    """CA-24: idempotencia en SQLite, igual que en memoria y en TimescaleDB."""
    barras = [_bar(2), _bar(3)]
    repo.upsert_bars(barras, _prov())
    segundo = repo.upsert_bars(barras, _prov())
    assert segundo.inserted == 0
    assert segundo.unchanged == 2
    assert repo.total_bars() == 2
    assert repo.revision_count() == 0


def test_sqlite_detecta_revision_del_proveedor(repo: SQLiteBarRepository) -> None:
    """CA-25: si el proveedor reescribe el histórico, queda registrado."""
    repo.upsert_bars([_bar(2)], _prov())
    report = repo.upsert_bars([_bar(2, cierre="5001.00")], _prov())
    assert report.revised == 1
    assert repo.revision_count() == 1


def test_sqlite_preserva_precision_decimal(repo: SQLiteBarRepository) -> None:
    """CA-26: los precios no pierden precisión al pasar por la base de datos.

    Almacenarlos como número en coma flotante introduciría error binario que
    se acumula en el cálculo de resultados.
    """
    bar = Bar(
        symbol="EURUSD",
        timeframe=Timeframe.D1,
        ts=utc(2025, 1, 2),
        source=DataSource.STOOQ,
        open=Decimal("1.03505"),
        high=Decimal("1.04001"),
        low=Decimal("1.03001"),
        close=Decimal("1.03805"),
    )
    repo.upsert_bars([bar], _prov())
    leida = repo.get_bars(
        "EURUSD", Timeframe.D1, DataSource.STOOQ, utc(2025, 1, 1), utc(2025, 2, 1)
    )[0]
    assert leida.close == Decimal("1.03805")
    assert str(leida.open) == "1.03505"


def test_sqlite_watermark(repo: SQLiteBarRepository) -> None:
    assert repo.get_watermark("US500", Timeframe.D1, DataSource.STOOQ) is None
    repo.upsert_bars([_bar(2), _bar(3)], _prov())
    assert repo.get_watermark("US500", Timeframe.D1, DataSource.STOOQ) == utc(2025, 1, 3)


def test_sqlite_inventario(repo: SQLiteBarRepository) -> None:
    """El inventario alimenta el panel de estado."""
    repo.upsert_bars([_bar(2), _bar(3)], _prov())
    inv = repo.inventory()
    assert len(inv) == 1
    assert inv[0]["symbol"] == "US500"
    assert inv[0]["bars"] == 2


def test_sqlite_lote_vacio_no_falla(repo: SQLiteBarRepository) -> None:
    assert repo.upsert_bars([], _prov()).total == 0


# --------------------------------------------------------------------- #
# Adaptador Yahoo — sustituye a Stooq tras el bloqueo del proveedor
# --------------------------------------------------------------------- #


def _filas_yahoo(n: int = 5):
    """Respuesta simulada de Yahoo, con el formato que devuelve yfinance."""
    from datetime import timedelta

    base = datetime(2025, 1, 2, tzinfo=timezone.utc)
    return [
        {
            "date": base + timedelta(days=i),
            "open": 5000.0 + i,
            "high": 5010.0 + i,
            "low": 4990.0 + i,
            "close": 5005.0 + i,
            "volume": 1_000_000 + i,
        }
        for i in range(n)
    ]


def test_yahoo_parsea_respuesta() -> None:
    """CA-32: el adaptador nuevo cumple el mismo contrato que el anterior."""
    from qq_core.adapters.yahoo import YahooProvider

    provider = YahooProvider(downloader=lambda s, a, b, i: _filas_yahoo(5))
    result = provider.fetch_bars(
        "^GSPC", "US500", Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1)
    )
    assert len(result.bars) == 5
    assert result.bars[0].source is DataSource.YAHOO
    assert result.provenance.provider_symbol == "^GSPC"


def test_yahoo_alinea_a_medianoche_utc() -> None:
    """Yahoo entrega fecha de sesión; `Bar` exige alineación a la rejilla."""
    from datetime import timedelta

    from qq_core.adapters.yahoo import YahooProvider

    filas = [
        {
            "date": datetime(2025, 1, 2, 14, 30, tzinfo=timezone.utc),
            "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.5,
            "volume": 1000,
        }
    ]
    provider = YahooProvider(downloader=lambda s, a, b, i: filas)
    bars = provider.fetch_bars(
        "X", "X", Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1)
    ).bars
    assert bars[0].ts == utc(2025, 1, 2)


def test_yahoo_descarta_filas_corruptas_sin_abortar() -> None:
    """Una fila con NaN no debe impedir la ingesta del resto del lote."""
    from qq_core.adapters.yahoo import YahooProvider

    filas = _filas_yahoo(3)
    filas[1]["close"] = float("nan")
    provider = YahooProvider(downloader=lambda s, a, b, i: filas)
    bars = provider.fetch_bars(
        "X", "X", Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1)
    ).bars
    assert len(bars) == 2


def test_yahoo_redondea_para_evitar_falsas_revisiones() -> None:
    """CA-33: dos descargas idénticas producen el mismo hash de contenido.

    Los flotantes de Yahoo arrastran ruido en decimales lejanos. Sin
    redondeo, cada descarga parecería una revisión del proveedor y el
    sistema emitiría alarmas continuas sin motivo.
    """
    from qq_core.adapters.yahoo import YahooProvider

    filas = [
        {
            "date": datetime(2025, 1, 2, tzinfo=timezone.utc),
            "open": 5000.0000000001, "high": 5010.0, "low": 4990.0,
            "close": 5005.00000000009, "volume": 1000,
        }
    ]
    provider = YahooProvider(downloader=lambda s, a, b, i: filas)
    a = provider.fetch_bars("X", "X", Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1))
    b = provider.fetch_bars("X", "X", Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1))
    assert a.bars[0].content_hash == b.bars[0].content_hash


def test_yahoo_error_de_red_es_reintentable() -> None:
    from qq_core.adapters.yahoo import YahooProvider
    from qq_core.ports.data_provider import ProviderError

    def falla(*args):
        raise ConnectionError("sin red")

    with pytest.raises(ProviderError):
        YahooProvider(downloader=falla).fetch_bars(
            "X", "X", Timeframe.D1, utc(2025, 1, 1), utc(2025, 2, 1)
        )


def test_catalogo_completo_mapeado_a_yahoo() -> None:
    """CA-34: los 23 instrumentos tienen equivalente en el proveedor activo."""
    assert len(catalog.with_yahoo_symbol()) == len(catalog.CATALOG)


def test_repositorio_detecta_fuente_principal(repo: SQLiteBarRepository) -> None:
    """El sistema no fija un proveedor por defecto: usa el que tenga datos."""
    assert repo.primary_source() is None
    repo.upsert_bars([_bar(2)], _prov())
    assert repo.primary_source() is DataSource.STOOQ


def test_repositorio_es_seguro_entre_hilos(tmp_path) -> None:
    """CA-41: la base de datos se puede usar desde varios hilos.

    El panel crea un hilo nuevo por cada interacción del usuario. Sin esta
    garantía, mover un control provoca un error y la aplicación se rompe.
    Reproducido en producción el 2026-08-11.
    """
    import threading

    repo = SQLiteBarRepository(tmp_path / "hilos.db")
    repo.upsert_bars([_bar(2), _bar(3)], _prov())

    errores: list[Exception] = []
    resultados: list[int] = []

    def leer() -> None:
        try:
            resultados.append(repo.total_bars())
            repo.inventory()
            repo.primary_source()
            repo.get_bars(
                "US500", Timeframe.D1, DataSource.STOOQ,
                utc(2025, 1, 1), utc(2025, 2, 1),
            )
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)

    hilos = [threading.Thread(target=leer) for _ in range(8)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, f"Fallos entre hilos: {errores}"
    assert resultados == [2] * 8
    repo.close()


def test_escritura_concurrente_no_corrompe(tmp_path) -> None:
    """Escrituras simultáneas desde varios hilos mantienen la coherencia."""
    import threading

    repo = SQLiteBarRepository(tmp_path / "escritura.db")
    errores: list[Exception] = []

    def escribir(dia: int) -> None:
        try:
            repo.upsert_bars([_bar(dia)], _prov())
        except Exception as exc:  # noqa: BLE001
            errores.append(exc)

    hilos = [threading.Thread(target=escribir, args=(d,)) for d in range(2, 12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()

    assert not errores, f"Fallos de escritura: {errores}"
    assert repo.total_bars() == 10
    repo.close()


# --------------------------------------------------------------------- #
# Catálogo editable por el operador
# --------------------------------------------------------------------- #


def test_desactivar_instrumento(tmp_path) -> None:
    """CA-49: el operador puede quitar activos sin tocar código."""
    from qq_core.catalog.overlay import CatalogOverlay, active_catalog

    ruta = tmp_path / "overlay.json"
    capa = CatalogOverlay.load(ruta)
    antes = len(active_catalog(capa))

    capa.disable("HK50", ruta)
    recargada = CatalogOverlay.load(ruta)
    assert len(active_catalog(recargada)) == antes - 1
    assert "HK50" not in {e.symbol for e in active_catalog(recargada)}


def test_reactivar_instrumento(tmp_path) -> None:
    from qq_core.catalog.overlay import CatalogOverlay, active_catalog

    ruta = tmp_path / "overlay.json"
    capa = CatalogOverlay.load(ruta)
    capa.disable("HK50", ruta)
    capa.enable("HK50", ruta)
    assert "HK50" in {e.symbol for e in active_catalog(CatalogOverlay.load(ruta))}


def test_anadir_instrumento(tmp_path) -> None:
    """CA-50: el operador puede añadir activos nuevos."""
    from qq_core.catalog.overlay import CatalogOverlay, active_catalog

    ruta = tmp_path / "overlay.json"
    capa = CatalogOverlay.load(ruta)
    antes = len(active_catalog(capa))

    capa.add_instrument("BTCUSD", "Bitcoin", "BTC-USD", "Cripto", path=ruta)
    activos = active_catalog(CatalogOverlay.load(ruta))
    assert len(activos) == antes + 1

    nuevo = next(e for e in activos if e.symbol == "BTCUSD")
    assert nuevo.yahoo_symbol == "BTC-USD"


def test_instrumento_anadido_tiene_financiacion_estimada(tmp_path) -> None:
    """Un instrumento añadido por el operador NUNCA se marca como medido.

    Declarar como verificado un coste que nadie ha comprobado en el terminal
    daría a un backtest una credibilidad que no le corresponde.
    """
    from qq_core.catalog.overlay import CatalogOverlay, active_catalog

    ruta = tmp_path / "overlay.json"
    capa = CatalogOverlay.load(ruta)
    capa.add_instrument("BTCUSD", "Bitcoin", "BTC-USD", "Cripto", path=ruta)

    nuevo = next(
        e for e in active_catalog(CatalogOverlay.load(ruta)) if e.symbol == "BTCUSD"
    )
    assert nuevo.instrument.financing.measured is False


def test_catalogo_base_no_se_modifica(tmp_path) -> None:
    """Las ediciones del operador no tocan el catálogo auditado.

    Si las ediciones sobrescribieran el archivo original, un error del
    operador destruiría el trabajo de auditoría del bróker.
    """
    from qq_core.catalog.overlay import CatalogOverlay

    ruta = tmp_path / "overlay.json"
    original = len(catalog.CATALOG)
    capa = CatalogOverlay.load(ruta)
    capa.disable("US500", ruta)
    capa.add_instrument("XXX", "Prueba", "XXX", "Otros", path=ruta)
    assert len(catalog.CATALOG) == original


def test_overlay_corrupto_no_rompe_el_sistema(tmp_path) -> None:
    """Un fichero de catálogo ilegible no debe impedir el arranque."""
    from qq_core.catalog.overlay import CatalogOverlay, active_catalog

    ruta = tmp_path / "roto.json"
    ruta.write_text("{esto no es json", encoding="utf-8")
    capa = CatalogOverlay.load(ruta)
    assert capa.disabled == set()
    assert len(active_catalog(capa)) == len(catalog.CATALOG)


# --------------------------------------------------------------------- #
# Twelve Data — la única fuente del catálogo con datos intradía
# --------------------------------------------------------------------- #


def _respuesta_twelvedata(n: int = 4, intradia: bool = False) -> str:
    """Respuesta simulada con el formato real de la API."""
    import json as _json

    valores = []
    for i in range(n):
        marca = (
            f"2026-08-14 {10 + i:02d}:00:00" if intradia
            else f"2026-08-{10 + i:02d}"
        )
        valores.append({
            "datetime": marca,
            "open": f"{5000 + i}", "high": f"{5010 + i}",
            "low": f"{4990 + i}", "close": f"{5005 + i}",
            "volume": "1000000",
        })
    return _json.dumps({"meta": {"symbol": "SPX"}, "values": valores,
                        "status": "ok"})


def test_twelvedata_soporta_intradia() -> None:
    """CA-89: es la única fuente que da velas de 5 minutos y 1 hora.

    Sin ella, las temporalidades cortas exigirían conectar el terminal del
    bróker. Con ella se resuelven sin depender de MetaTrader.
    """
    from qq_core.adapters.twelvedata import TwelveDataProvider

    capacidades = TwelveDataProvider(api_key="x").capabilities
    assert Timeframe.M5 in capacidades.timeframes
    assert Timeframe.H1 in capacidades.timeframes
    assert Timeframe.D1 in capacidades.timeframes


def test_twelvedata_parsea_datos_diarios() -> None:
    from qq_core.adapters.twelvedata import TwelveDataProvider

    provider = TwelveDataProvider(
        api_key="x", fetcher=lambda url: _respuesta_twelvedata(4)
    )
    resultado = provider.fetch_bars(
        "SPX", "US500", Timeframe.D1, utc(2026, 8, 1), utc(2026, 9, 1)
    )
    assert len(resultado.bars) == 4
    assert resultado.bars[0].source is DataSource.TWELVEDATA


def test_twelvedata_parsea_datos_intradia() -> None:
    """Las velas horarias deben conservar la hora, no truncarse a medianoche."""
    from qq_core.adapters.twelvedata import TwelveDataProvider

    provider = TwelveDataProvider(
        api_key="x", fetcher=lambda url: _respuesta_twelvedata(3, intradia=True)
    )
    resultado = provider.fetch_bars(
        "SPX", "US500", Timeframe.H1, utc(2026, 8, 14), utc(2026, 8, 15)
    )
    assert len(resultado.bars) == 3
    assert resultado.bars[0].ts.hour == 10


def test_twelvedata_sin_clave_avisa_claramente(monkeypatch) -> None:
    """CA-90: un fallo de configuración debe explicarse, no fallar sin más.

    La prueba borra la variable de entorno antes de ejecutarse. Sin eso, su
    resultado dependía de si la máquina tenía clave configurada: pasaba en el
    equipo de desarrollo y fallaba en el del operador tras configurar Twelve
    Data. Una prueba que depende del entorno no es una prueba.
    """
    from qq_core.adapters.twelvedata import CLAVE_ENV, TwelveDataProvider
    from qq_core.ports.data_provider import ProviderError

    monkeypatch.delenv(CLAVE_ENV, raising=False)

    provider = TwelveDataProvider(api_key="")
    assert not provider.is_configured

    with pytest.raises(ProviderError, match="clave"):
        provider.fetch_bars(
            "SPX", "US500", Timeframe.D1, utc(2026, 1, 1), utc(2026, 2, 1)
        )


def test_twelvedata_distingue_cuota_de_error_permanente() -> None:
    """CA-91: agotar la cuota es recuperable; un símbolo inválido no.

    Reintentar una petición rechazada por símbolo inexistente sólo consume
    cuota sin posibilidad de éxito.
    """
    import json as _json

    from qq_core.adapters.twelvedata import TwelveDataProvider
    from qq_core.ports.data_provider import ProviderContractError, ProviderError

    cuota = _json.dumps(
        {"status": "error", "message": "You have run out of API credits"}
    )
    provider_cuota = TwelveDataProvider(api_key="x", fetcher=lambda u: cuota)
    with pytest.raises(ProviderError) as exc_cuota:
        provider_cuota.fetch_bars(
            "SPX", "US500", Timeframe.D1, utc(2026, 1, 1), utc(2026, 2, 1)
        )
    assert not isinstance(exc_cuota.value, ProviderContractError)

    invalido = _json.dumps({"status": "error", "message": "symbol not found"})
    provider_malo = TwelveDataProvider(api_key="x", fetcher=lambda u: invalido)
    with pytest.raises(ProviderContractError):
        provider_malo.fetch_bars(
            "NOEXISTE", "X", Timeframe.D1, utc(2026, 1, 1), utc(2026, 2, 1)
        )


def test_la_clave_no_queda_registrada_en_la_procedencia() -> None:
    """CA-92: una credencial nunca debe acabar en la base de datos.

    La procedencia se almacena y aparece en informes. Guardar la clave allí
    la expondría a cualquiera con acceso al fichero.
    """
    from qq_core.adapters.twelvedata import TwelveDataProvider

    secreta = "CLAVE_SECRETA_12345"
    provider = TwelveDataProvider(
        api_key=secreta, fetcher=lambda url: _respuesta_twelvedata(2)
    )
    resultado = provider.fetch_bars(
        "SPX", "US500", Timeframe.D1, utc(2026, 8, 1), utc(2026, 9, 1)
    )
    assert secreta not in str(resultado.provenance.request_params)
    assert secreta not in resultado.provenance.model_dump_json()


def test_catalogo_completo_mapeado_a_twelvedata() -> None:
    """Los 23 instrumentos deben existir también en la fuente intradía."""
    assert len(catalog.with_twelvedata_symbol()) == len(catalog.CATALOG)


def test_limite_de_peticiones_declarado() -> None:
    """El plan gratuito limita a 8 por minuto; debe estar declarado.

    Ocultarlo llevaría al orquestador a chocar contra el límite en lugar de
    respetarlo.
    """
    from qq_core.adapters.twelvedata import TwelveDataProvider

    capacidades = TwelveDataProvider(api_key="x").capabilities
    assert capacidades.rate_limit_per_minute == 8
    assert any("intradía" in n for n in capacidades.notes)



def test_twelvedata_no_depende_del_entorno_de_la_maquina(monkeypatch) -> None:
    """CA-92: el comportamiento no puede cambiar según cómo esté el equipo.

    Con `api_key=""` el proveedor está sin configurar AUNQUE la variable de
    entorno exista. Con `api_key=None` sí la consulta. Sin esta distinción, el
    mismo código se comportaba de forma distinta en cada ordenador.
    """
    from qq_core.adapters.twelvedata import CLAVE_ENV, TwelveDataProvider

    monkeypatch.setenv(CLAVE_ENV, "clave_del_entorno")

    assert not TwelveDataProvider(api_key="").is_configured
    assert TwelveDataProvider(api_key=None).is_configured
    assert TwelveDataProvider(api_key="explicita").is_configured
