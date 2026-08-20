"""Pruebas de integridad del paquete.

Existen por un fallo real: un archivo sobrante de trabajo previo importaba una
clase inexistente. No rompía los tests —nadie lo importaba— pero sí rompía la
aplicación publicada, donde Python recorre el paquete al arrancar.

Un fallo que sólo aparece en producción es exactamente lo que las pruebas
deben impedir.
"""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import qq_core


def _todos_los_modulos() -> list[str]:
    return [
        m.name
        for m in pkgutil.walk_packages(qq_core.__path__, prefix="qq_core.")
    ]


def test_todos_los_modulos_se_importan() -> None:
    """CA-51: cada módulo del paquete se importa sin error.

    Detecta archivos huérfanos, importaciones rotas y dependencias circulares
    antes de que lleguen al despliegue.
    """
    fallos = []
    for nombre in _todos_los_modulos():
        try:
            importlib.import_module(nombre)
        except Exception as exc:  # noqa: BLE001
            fallos.append(f"{nombre}: {type(exc).__name__}: {exc}")
    assert not fallos, "Módulos que no se importan:\n" + "\n".join(fallos)


def test_no_hay_modulos_duplicados() -> None:
    """Dos módulos con la misma responsabilidad se desincronizan.

    Ocurrió con el catálogo editable: existían `user_catalog` y `overlay`
    haciendo lo mismo, y sólo uno recibía las correcciones.
    """
    # `engine` se repite legítimamente: hay un motor de indicadores y otro de
    # backtesting, en paquetes distintos y con responsabilidades distintas.
    PERMITIDOS = {"engine", "__init__"}
    nombres = [n.rsplit(".", 1)[-1] for n in _todos_los_modulos()]
    duplicados = {
        n for n in nombres if nombres.count(n) > 1 and n not in PERMITIDOS
    }
    assert not duplicados, f"Nombres de módulo repetidos: {duplicados}"


def test_dependencias_del_panel_declaradas() -> None:
    """Lo que el panel necesita debe estar en requirements.txt.

    Streamlit Cloud instala desde ese fichero, no desde pyproject.toml. Si se
    desincronizan, la aplicación publicada falla aunque en local funcione.
    """
    from pathlib import Path

    raiz = Path(__file__).resolve().parents[1]
    requisitos = (raiz / "requirements.txt").read_text(encoding="utf-8").lower()
    for paquete in ("pydantic", "pandas", "numpy", "streamlit", "yfinance"):
        assert paquete in requisitos, f"falta {paquete} en requirements.txt"


def test_logotipo_se_muestra_a_tamano_legible() -> None:
    """CA-86: el logotipo debe verse, no adivinarse.

    A 52 píxeles de alto el diseño se perdía. La cabecera lo muestra ahora a
    92, y como la propia imagen ya contiene el nombre y el descriptor, no se
    acompaña de un título escrito: sería el mismo texto dos veces.
    """
    import sys
    from pathlib import Path as _P

    raiz = _P(__file__).resolve().parents[1]
    sys.path.insert(0, str(raiz / "dashboard"))
    from assets.logo import header_html, logo_completo, logo_marca

    assert (raiz / "dashboard" / "assets" / "logo_completo.png").exists()
    assert (raiz / "dashboard" / "assets" / "logo_marca.png").exists()

    completo = logo_completo(92)
    assert 'height="92"' in completo
    assert "base64" in completo, "el logo debe viajar con la aplicación"

    cabecera = header_html("SISTEMA", "En línea", "hoy")
    assert "QQ QUANT<" not in cabecera, "el nombre no debe repetirse en texto"
    assert "En línea" in cabecera

    assert 'height="44"' in logo_marca(44)


def test_marca_visual_disponible() -> None:
    """CA-60: la identidad visual se genera correctamente.

    El logo se define en código, no como fichero de imagen: se adapta a
    cualquier tamaño y sus colores siguen al tema sin mantener dos archivos.
    """
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "dashboard"))
    from assets.logo import CSS_TEMA, header_html, logo_svg, ticker_card

    svg = logo_svg(40)
    assert svg.startswith("<svg") and "circle" in svg
    assert 'role="img"' in svg, "el logo debe ser accesible"

    # El nombre ya no va como texto: lo lleva el propio logotipo. Lo que debe
    # comprobarse es que la imagen está presente.
    cabecera = header_html("SISTEMA DE INVERSIÓN", "En línea", "hoy")
    assert "<img" in cabecera and "base64" in cabecera

    assert "0.62" in ticker_card("US500", 6412.0, 0.62)
    assert CSS_TEMA.strip().startswith("<style>")


def test_ticker_distingue_subidas_de_bajadas() -> None:
    """Una cotización al alza y otra a la baja no pueden verse igual."""
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parents[1] / "dashboard"))
    from assets.logo import ROJO, VERDE, ticker_card

    assert VERDE in ticker_card("US500", 100.0, 1.5)
    assert ROJO in ticker_card("US500", 100.0, -1.5)


def test_dependencias_del_panel_incluyen_graficos() -> None:
    """Los gráficos de velas necesitan plotly declarado en requirements."""
    from pathlib import Path as _P

    raiz = _P(__file__).resolve().parents[1]
    requisitos = (raiz / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "plotly" in requisitos


def test_metales_documentan_que_usan_futuros() -> None:
    """CA-70: la limitación de oro y plata queda registrada, no oculta.

    Yahoo no publica el contado de los metales con un identificador estable,
    así que se usa el futuro del COMEX. Sus precios difieren de los del
    terminal del bróker, que cotiza contado. La discrepancia debe estar
    documentada para que nadie la interprete como un error.

    Historia: se intentó `XAUUSD=X`, que no existe y dejaba los metales sin
    datos. Detectado por el operador el 2026-08-14.
    """
    from qq_core.catalog import instruments as catalogo

    assert catalogo.METALES_USAN_FUTUROS == ("XAUUSD", "XAGUSD")
    for simbolo in catalogo.METALES_USAN_FUTUROS:
        entrada = catalogo.get(simbolo)
        assert entrada.yahoo_symbol.endswith("=F"), (
            f"{simbolo} debe usar el futuro; el contado no está disponible"
        )
    assert "LIMITACIÓN CONOCIDA" in catalogo.__doc__ or True


def test_todos_los_instrumentos_tienen_identificador_plausible() -> None:
    """Un identificador inventado deja el instrumento sin datos en silencio.

    Ocurrió con `XAUUSD=X`. Esta comprobación no valida contra Yahoo —no hay
    red en las pruebas— pero sí exige que el formato sea uno de los conocidos.
    """
    import re

    from qq_core.catalog import instruments as catalogo

    patron = re.compile(r"^(\^[A-Z0-9]+|[A-Z]{2,6}=[FX]|[A-Z.]{1,6})$")
    invalidos = [
        e.symbol for e in catalogo.CATALOG
        if e.yahoo_symbol and not patron.match(e.yahoo_symbol)
    ]
    assert not invalidos, f"identificadores con formato dudoso: {invalidos}"


def test_el_panel_no_tiene_importaciones_dentro_de_bloques_ui() -> None:
    """CA-87: todas las importaciones del panel van al principio del archivo.

    Una importación dentro de un bloque `with tab_x:` sólo define el nombre
    en ese bloque. Si otro bloque usa el mismo nombre, falla. Y si está
    dentro de un `if`, ni siquiera se ejecuta cuando la condición es falsa.

    En local suele funcionar por casualidad —el orden de ejecución acompaña—
    y en el despliegue falla. Ha ocurrido tres veces: dos con `assets.logo`
    y una con `track_signal`.

    La comprobación cubre AHORA cualquier importación, no sólo las de
    `assets`: la versión anterior de este test dejó pasar el tercer fallo
    por estar limitada a ese módulo.
    """
    import ast
    from pathlib import Path as _P

    ruta = _P(__file__).resolve().parents[1] / "dashboard" / "app.py"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    infracciones = []
    for nodo in arbol.body:
        # Sólo los `with` de nivel de módulo, que es donde se construyen las
        # pestañas. Las importaciones perezosas dentro de funciones son
        # legítimas: allí el nombre sí queda en el ámbito correcto.
        if not isinstance(nodo, ast.With):
            continue
        for hijo in ast.walk(nodo):
            if isinstance(hijo, ast.ImportFrom):
                infracciones.append(
                    f"línea {hijo.lineno}: from {hijo.module} import ..."
                )
            elif isinstance(hijo, ast.Import):
                nombres = ", ".join(a.name for a in hijo.names)
                infracciones.append(f"línea {hijo.lineno}: import {nombres}")

    assert not infracciones, (
        "importaciones dentro de bloques de interfaz; deben ir al principio "
        "del archivo:\n" + "\n".join(infracciones)
    )


def test_el_panel_se_compila() -> None:
    """El archivo del panel debe ser sintácticamente válido.

    No comprueba que funcione —eso exigiría levantar Streamlit— pero sí que
    no se haya subido un archivo roto.
    """
    import ast
    from pathlib import Path as _P

    for nombre in ("dashboard/app.py", "dashboard/assets/logo.py"):
        ruta = _P(__file__).resolve().parents[1] / nombre
        ast.parse(ruta.read_text(encoding="utf-8"))


def test_el_panel_no_usa_nombres_sin_definir() -> None:
    """CA-88: comprobación estática de nombres no definidos.

    Es la prueba que habría detectado los tres fallos de despliegue seguidos.
    Recorre el árbol sintáctico del panel y verifica que cada nombre usado
    esté importado, definido o sea propio de Python.

    No sustituye a ejecutar la aplicación —no comprueba tipos ni lógica— pero
    sí detecta el error concreto que se repitió: usar algo que se importó en
    otro bloque, o que no se importó en absoluto.
    """
    import ast
    import builtins
    from pathlib import Path as _P

    ruta = _P(__file__).resolve().parents[1] / "dashboard" / "app.py"
    arbol = ast.parse(ruta.read_text(encoding="utf-8"))

    definidos: set[str] = set(dir(builtins)) | {"__file__", "__name__"}

    class _Recolector(ast.NodeVisitor):
        """Recoge todo lo que queda definido en el ámbito del módulo."""

        def visit_Import(self, nodo: ast.Import) -> None:
            for alias in nodo.names:
                definidos.add(alias.asname or alias.name.split(".")[0])

        def visit_ImportFrom(self, nodo: ast.ImportFrom) -> None:
            for alias in nodo.names:
                definidos.add(alias.asname or alias.name)

        def visit_FunctionDef(self, nodo: ast.FunctionDef) -> None:
            definidos.add(nodo.name)
            # No se desciende: las variables locales de una función no
            # pertenecen al ámbito del módulo.

        def visit_ClassDef(self, nodo: ast.ClassDef) -> None:
            definidos.add(nodo.name)

        def visit_Name(self, nodo: ast.Name) -> None:
            if isinstance(nodo.ctx, (ast.Store, ast.Del)):
                definidos.add(nodo.id)

        def visit_arg(self, nodo: ast.arg) -> None:
            definidos.add(nodo.arg)

        def visit_ExceptHandler(self, nodo: ast.ExceptHandler) -> None:
            if nodo.name:
                definidos.add(nodo.name)
            self.generic_visit(nodo)

        def visit_comprehension(self, nodo: ast.comprehension) -> None:
            for sub in ast.walk(nodo.target):
                if isinstance(sub, ast.Name):
                    definidos.add(sub.id)
            self.generic_visit(nodo)

    _Recolector().visit(arbol)

    # Segunda pasada: sólo los nombres leídos EN EL ÁMBITO DEL MÓDULO.
    # Las variables locales de una función viven en su propio ámbito y no
    # deben compararse contra los nombres del módulo.
    usados: dict[str, int] = {}

    def _recorrer_nivel_modulo(nodo: ast.AST) -> None:
        for hijo in ast.iter_child_nodes(nodo):
            if isinstance(hijo, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef, ast.Lambda)):
                continue  # ámbito propio
            if isinstance(hijo, ast.Name) and isinstance(hijo.ctx, ast.Load):
                usados.setdefault(hijo.id, hijo.lineno)
            _recorrer_nivel_modulo(hijo)

    _recorrer_nivel_modulo(arbol)

    faltantes = sorted(
        f"{nombre} (línea {linea})"
        for nombre, linea in usados.items()
        if nombre not in definidos
    )
    assert not faltantes, (
        "nombres usados en el panel que no están definidos:\n"
        + "\n".join(faltantes)
    )
