"""Pruebas del Módulo 05 — validación estadística y control de sobreajuste.

La prueba más importante del archivo es `test_ruido_puro_no_supera_la_correccion`.
Verifica la propiedad que justifica la existencia del módulo entero: que 207
series sin ninguna capacidad predictiva, entre las cuales la mejor exhibe un
Sharpe llamativo por puro azar, sean rechazadas.

Si esa prueba dejara de pasar, el sistema habría vuelto a ser capaz de
presentar ruido como si fuera una estrategia.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from qq_core.validation import (
    Trial,
    TrialRegistry,
    Verdict,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    probabilistic_sharpe_ratio,
    probability_of_backtest_overfitting,
    purged_kfold_splits,
    sharpe_stats,
    superior_predictive_ability,
    validate_candidates,
)


@pytest.fixture
def ruido_207() -> dict[tuple[str, str], pd.Series]:
    """207 combinaciones de puro ruido, como las que evalúa el sistema."""
    rng = np.random.default_rng(20260818)
    m = rng.normal(0.0, 0.01, size=(2500, 207))
    return {
        (f"est{j % 9}", f"sym{j // 9}"): pd.Series(m[:, j]) for j in range(207)
    }


# --------------------------------------------------------------------------- #
# CA-40 a CA-44: la propiedad central del módulo
# --------------------------------------------------------------------------- #


def test_ruido_puro_produce_un_sharpe_enganosamente_alto(ruido_207) -> None:
    """CA-40: documenta el problema que el módulo resuelve.

    Con 207 series aleatorias sin ninguna señal, la mejor exhibe un Sharpe
    anualizado claramente positivo. Esta prueba no comprueba código nuestro:
    comprueba un hecho estadístico, y existe para que quede escrito en el
    repositorio por qué la corrección es obligatoria.
    """
    mejor = max(
        sharpe_stats(s).sharpe_annual for s in ruido_207.values()
    )
    assert mejor > 0.5, (
        "si esto falla, la semilla cambió; el punto sigue siendo que buscar "
        "entre 207 candidatos produce Sharpes altos sin ninguna señal"
    )


def test_ruido_puro_no_supera_la_correccion(ruido_207) -> None:
    """CA-41: LA PRUEBA CRÍTICA. Ninguna serie de ruido puede validarse.

    Es la razón de ser del Módulo 05. Si esta prueba falla, el sistema puede
    volver a recomendar operar sobre resultados que son azar.
    """
    informe = validate_candidates(ruido_207, n_trials=207, n_bootstrap=300, seed=1)

    assert informe.n_validated == 0
    assert informe.spa is not None
    assert not informe.spa.is_significant
    assert "NINGUNA" in informe.headline


def test_el_liston_por_azar_crece_con_el_numero_de_ensayos() -> None:
    """CA-42: cuantos más candidatos se prueban, más alto el listón.

    Es el mecanismo que hace la corrección más exigente al ampliar la búsqueda.
    Si no creciera, evaluar más combinaciones sería gratis.
    """
    varianza = 0.0025
    umbrales = [expected_max_sharpe(n, varianza) for n in (10, 100, 207, 1000)]
    assert umbrales == sorted(umbrales)
    assert umbrales[-1] > umbrales[0]


def test_senal_real_y_fuerte_si_se_valida() -> None:
    """CA-43: la corrección no es un rechazo automático.

    Una prueba que rechazara todo sería inútil. Se verifica que una serie con
    Sharpe verdadero de 2.2 sobre ocho años sí se valida.
    """
    rng = np.random.default_rng(4)
    n, k = 2000, 20
    m = rng.normal(0.0, 0.01, size=(n, k))
    m[:, 0] += 2.2 / np.sqrt(252) * 0.01

    combos = {(f"est{j}", "US500"): pd.Series(m[:, j]) for j in range(k)}
    informe = validate_candidates(combos, n_trials=k, n_bootstrap=400, seed=2)

    assert informe.n_validated >= 1
    ganadora = next(v for v in informe.verdicts if v.strategy == "est0")
    assert ganadora.verdict is Verdict.VALIDATED


def test_muestra_insuficiente_no_es_un_aprobado() -> None:
    """CA-44: no pronunciarse nunca equivale a validar.

    Es la confusión que convierte "no hay datos" en "no hay problema".
    """
    combos = {("est", "sym"): pd.Series(np.random.normal(0, 0.01, 10))}
    informe = validate_candidates(combos, n_trials=1, n_bootstrap=50)

    for v in informe.verdicts:
        assert not v.verdict.is_tradeable


# --------------------------------------------------------------------------- #
# CA-45 a CA-47: Sharpe, PSR y DSR
# --------------------------------------------------------------------------- #


def test_sharpe_clasico_difiere_del_geometrico() -> None:
    """CA-45: se recalcula el Sharpe en lugar de reutilizar el del motor.

    Documenta por qué: las fórmulas del PSR y el DSR asumen el Sharpe clásico.
    """
    rng = np.random.default_rng(9)
    r = pd.Series(rng.normal(0.0005, 0.012, 1500))
    st = sharpe_stats(r)

    assert st.is_usable
    assert st.n == 1500
    assert abs(st.sharpe_annual - st.sharpe * np.sqrt(252)) < 1e-9


def test_tipo_sin_riesgo_reduce_el_sharpe() -> None:
    """CA-46: el tipo sin riesgo no es decorativo.

    Con tipos positivos, asumir cero infla todos los Sharpe del sistema.
    """
    rng = np.random.default_rng(11)
    r = pd.Series(rng.normal(0.0004, 0.01, 2000))

    sin_rf = sharpe_stats(r, risk_free_annual=0.0)
    con_rf = sharpe_stats(r, risk_free_annual=0.04)

    assert con_rf.sharpe_annual < sin_rf.sharpe_annual


def test_deflated_sharpe_es_mas_exigente_que_probabilistic() -> None:
    """CA-47: descontar la búsqueda siempre endurece el criterio."""
    rng = np.random.default_rng(13)
    st = sharpe_stats(pd.Series(rng.normal(0.0006, 0.01, 2000)))

    psr = probabilistic_sharpe_ratio(st, benchmark_sharpe=0.0)
    dsr = deflated_sharpe_ratio(st, n_trials=207, variance_of_trials=0.002)

    assert psr is not None and dsr is not None
    assert dsr <= psr


# --------------------------------------------------------------------------- #
# CA-48 a CA-50: SPA y PBO
# --------------------------------------------------------------------------- #


def test_spa_es_reproducible_con_la_misma_semilla() -> None:
    """CA-48: un resultado no reproducible no es auditable."""
    rng = np.random.default_rng(17)
    m = rng.normal(0, 0.01, size=(500, 10))

    a = superior_predictive_ability(m, n_bootstrap=200, seed=99)
    b = superior_predictive_ability(m, n_bootstrap=200, seed=99)

    assert a.p_value == b.p_value


def test_pbo_distingue_ruido_de_senal_pero_no_basta_sola() -> None:
    """CA-49: la PBO discrimina, pero NO llega a 0.5 con ruido puro.

    Documenta una limitación medida, no supuesta. Las dos mitades salen de la
    misma muestra finita, así que una serie con media global afortunada lo es
    en ambas mitades. Esa suerte compartida infla la aparente persistencia.

    Consecuencia: la PBO es la más débil de las tres pruebas y no debe usarse
    aislada. El SPA es quien rechaza correctamente este mismo conjunto.
    """
    rng = np.random.default_rng(23)
    ruido = rng.normal(0, 0.01, size=(1200, 40))
    con_senal = ruido.copy()
    con_senal[:, 0] += 0.002

    pbo_ruido = probability_of_backtest_overfitting(ruido, n_blocks=8).pbo
    pbo_senal = probability_of_backtest_overfitting(con_senal, n_blocks=8).pbo

    # Discrimina en la dirección correcta...
    assert pbo_ruido > pbo_senal
    # ...pero NO alcanza 0.5, que es la limitación documentada.
    assert pbo_ruido < 0.50

    # Y el SPA sí rechaza este conjunto, que es el punto de usar tres pruebas.
    spa = superior_predictive_ability(ruido, n_bootstrap=200, seed=5)
    assert not spa.is_significant


def test_pbo_baja_cuando_hay_senal_persistente() -> None:
    """CA-50: con señal real y estable, la selección sí generaliza."""
    rng = np.random.default_rng(29)
    m = rng.normal(0, 0.01, size=(1200, 20))
    m[:, 0] += 0.002

    r = probability_of_backtest_overfitting(m, n_blocks=8)

    assert r.pbo < 0.20
    assert r.is_acceptable


# --------------------------------------------------------------------------- #
# CA-51 a CA-52: purga y embargo
# --------------------------------------------------------------------------- #


def test_purga_elimina_observaciones_contiguas_al_test() -> None:
    """CA-51: sin purga, el conjunto de prueba está contaminado.

    Una señal con horizonte de un mes sigue realizándose durante 22 sesiones.
    Esas sesiones no pueden estar en entrenamiento si caen dentro del test.
    """
    repartos = purged_kfold_splits(1000, n_folds=5, holding_periods=22)

    for r in repartos:
        assert r.purged > 0
        # Ningún índice de entrenamiento está a menos de 22 del test.
        if r.train.size and r.test.size:
            distancia = np.min(np.abs(r.train[:, None] - r.test[None, :]))
            assert distancia > 22


def test_purga_sin_solapamiento_entre_train_y_test() -> None:
    """CA-52: conjuntos disjuntos, condición mínima de cualquier validación."""
    for r in purged_kfold_splits(800, n_folds=4, holding_periods=10):
        assert set(r.train.tolist()).isdisjoint(set(r.test.tolist()))


# --------------------------------------------------------------------------- #
# CA-53 a CA-55: registro de ensayos
# --------------------------------------------------------------------------- #


def test_registro_deduplica_por_configuracion(tmp_path) -> None:
    """CA-53: repetir la misma configuración no son dos ensayos."""
    reg = TrialRegistry(tmp_path / "trials.db")
    t = Trial("tf", "1.0", "US500", params={"ventana": 20})

    assert reg.record(t) is True
    assert reg.record(t) is False
    assert reg.count() == 1

    reg.close()


def test_cambiar_un_parametro_es_un_ensayo_nuevo(tmp_path) -> None:
    """CA-54: mover un umbral y volver a mirar es una prueba adicional.

    Es el mecanismo por el que el número real de ensayos crece sin que nadie
    lo perciba, y la razón de que el registro exista.
    """
    reg = TrialRegistry(tmp_path / "trials.db")
    reg.record(Trial("tf", "1.0", "US500", params={"ventana": 20}))
    reg.record(Trial("tf", "1.0", "US500", params={"ventana": 50}))

    assert reg.count() == 2
    reg.close()


def test_el_registro_declara_que_es_una_cota_inferior(tmp_path) -> None:
    """CA-55: la limitación debe ser visible, no estar sólo en un comentario.

    Los ensayos anteriores al registro no se pueden recuperar. Ocultarlo haría
    que las correcciones parecieran más sólidas de lo que son.
    """
    reg = TrialRegistry(tmp_path / "trials.db")
    reg.record(Trial("tf", "1.0", "US500"))

    resumen = reg.summary()
    assert resumen["es_cota_inferior"] is True
    assert "optimistas" in resumen["advertencia"]

    reg.close()


def test_informe_declara_sus_limitaciones(ruido_207) -> None:
    """CA-56: el informe nunca se presenta sin sus salvedades."""
    informe = validate_candidates(ruido_207, n_trials=207, n_bootstrap=100)
    avisos = informe.caveats()

    assert any("cota inferior" in a for a in avisos)
    assert any("-9%" in a for a in avisos)
    assert any("NECESARIA" in a for a in avisos)


# --------------------------------------------------------------------------- #
# CA-93 a CA-95: benchmark comprar y mantener
# --------------------------------------------------------------------------- #


def test_beta_disfrazada_de_alfa_se_detecta_con_el_benchmark_correcto() -> None:
    """CA-93: LA PRUEBA que justifica el cambio de benchmark.

    Cincuenta estrategias SIN ninguna habilidad, que sólo capturan parte de un
    mercado alcista. Contra cero, el contraste declara evidencia estadística.
    Contra comprar y mantener, no encuentra nada.

    Medido: p = 0.0020 contra cero, p = 0.8862 contra comprar y mantener.

    El benchmark anterior confundía exposición al mercado con capacidad
    predictiva. Era un defecto de diseño, no una elección conservadora.
    """
    rng = np.random.default_rng(3)
    n = 2500
    mercado = pd.Series(rng.normal(0.0006, 0.010, n))

    combos = {}
    for j in range(50):
        beta = 0.4 + 0.5 * rng.random()
        combos[(f"est{j % 9}", f"sym{j // 9}")] = mercado * beta + rng.normal(
            0, 0.004, n
        )
    referencia = {f"sym{j // 9}": mercado for j in range(50)}

    contra_cero = validate_candidates(combos, n_trials=50, n_bootstrap=500, seed=1)
    contra_mercado = validate_candidates(
        combos, n_trials=50, n_bootstrap=500, seed=1, benchmark_by_symbol=referencia
    )

    assert contra_cero.spa is not None and contra_mercado.spa is not None
    assert contra_cero.spa.is_significant
    assert not contra_mercado.spa.is_significant


def test_habilidad_real_sobrevive_al_benchmark_correcto() -> None:
    """CA-94: el benchmark nuevo no rechaza todo indiscriminadamente.

    Una estrategia que de verdad supera al mercado sigue detectándose.
    """
    rng = np.random.default_rng(11)
    n = 2500
    mercado = pd.Series(rng.normal(0.0004, 0.010, n))

    combos = {}
    for j in range(30):
        combos[(f"est{j}", "sym0")] = mercado * 0.8 + rng.normal(0, 0.004, n)
    # Una con exceso genuino sobre el mercado.
    combos[("estrella", "sym0")] = mercado + rng.normal(0.0008, 0.004, n)

    referencia = {"sym0": mercado}
    r = validate_candidates(
        combos, n_trials=31, n_bootstrap=500, seed=5, benchmark_by_symbol=referencia
    )

    assert r.spa is not None
    assert r.spa.is_significant


def test_el_informe_declara_contra_que_se_contrasto() -> None:
    """CA-95: quien lea el informe debe saber cuál fue el listón.

    Sin esa declaración, un p-valor de 0.001 contra cero y otro contra el
    mercado parecen el mismo resultado, y significan cosas muy distintas.
    """
    rng = np.random.default_rng(17)
    combos = {("e", "s"): pd.Series(rng.normal(0.0003, 0.01, 800))}

    sin = validate_candidates(combos, n_trials=1, n_bootstrap=100)
    con = validate_candidates(
        combos,
        n_trials=1,
        n_bootstrap=100,
        benchmark_by_symbol={"s": pd.Series(rng.normal(0.0003, 0.01, 800))},
    )

    assert sin.benchmark_is_buy_and_hold is False
    assert con.benchmark_is_buy_and_hold is True
    assert any("NO OPERAR" in a for a in sin.caveats())
    assert not any("NO OPERAR" in a for a in con.caveats())
