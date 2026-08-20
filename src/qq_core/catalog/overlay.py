"""Catálogo editable por el usuario.

PROBLEMA QUE RESUELVE
---------------------
El catálogo base (`instruments.py`) es código: define los 23 instrumentos
auditados con sus parámetros verificados. Editarlo requiere tocar Python y
volver a desplegar.

El operador necesita poder activar y desactivar instrumentos, y añadir alguno
nuevo, sin programar. Esta capa lo permite guardando las modificaciones en un
fichero JSON aparte.

DISEÑO: SUPERPOSICIÓN, NO SUSTITUCIÓN
--------------------------------------
Las modificaciones NO reescriben el catálogo base. Se guardan como una capa
encima:

  - `disabled`: símbolos del catálogo base que el operador ha desactivado.
  - `added`: instrumentos nuevos que ha añadido.

Así el catálogo base sigue siendo la referencia auditada, y en cualquier
momento se puede volver a él descartando la capa. Si las ediciones
sobrescribieran el archivo original, un error del operador destruiría el
trabajo de auditoría.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from qq_core.catalog import instruments as base
from qq_core.domain.instrument import CFD, FinancingTerms

OVERLAY_PATH = Path("catalogo_usuario.json")
"""Fichero donde se guardan las modificaciones del operador."""


@dataclass
class CatalogOverlay:
    """Modificaciones del operador sobre el catálogo base.

    Attributes:
        disabled: Símbolos desactivados. Siguen en el catálogo pero no se
            ingestan ni generan señales.
        added: Instrumentos añadidos por el operador, con sus datos mínimos.
    """

    disabled: set[str]
    added: dict[str, dict[str, Any]]

    @classmethod
    def load(cls, path: Path | str = OVERLAY_PATH) -> "CatalogOverlay":
        """Carga las modificaciones guardadas.

        Si el fichero no existe o está corrupto, devuelve una capa vacía en
        lugar de fallar: un catálogo de usuario ilegible no debe impedir que el
        sistema arranque con el catálogo base.
        """
        ruta = Path(path)
        if not ruta.exists():
            return cls(disabled=set(), added={})
        try:
            datos = json.loads(ruta.read_text(encoding="utf-8"))
            return cls(
                disabled=set(datos.get("disabled", [])),
                added=datos.get("added", {}),
            )
        except (json.JSONDecodeError, OSError):
            return cls(disabled=set(), added={})

    def save(self, path: Path | str = OVERLAY_PATH) -> None:
        """Guarda las modificaciones en disco."""
        Path(path).write_text(
            json.dumps(
                {"disabled": sorted(self.disabled), "added": self.added},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def disable(self, symbol: str, path: Path | str = OVERLAY_PATH) -> None:
        """Desactiva un instrumento del universo operable."""
        self.disabled.add(symbol.upper())
        self.save(path)

    def enable(self, symbol: str, path: Path | str = OVERLAY_PATH) -> None:
        """Reactiva un instrumento previamente desactivado."""
        self.disabled.discard(symbol.upper())
        self.save(path)

    def add_instrument(
        self,
        symbol: str,
        name: str,
        yahoo_symbol: str,
        group: str,
        currency: str = "USD",
        mt5_symbol: str | None = None,
        path: Path | str = OVERLAY_PATH,
    ) -> None:
        """Añade un instrumento nuevo al universo.

        Args:
            symbol: Símbolo canónico interno, en mayúsculas.
            name: Nombre legible.
            yahoo_symbol: Identificador en el proveedor de datos.
            group: Categoría para agrupar en el panel.
            currency: Divisa de cotización.
            mt5_symbol: Símbolo en el bróker, si es operable allí.

        Note:
            El instrumento se crea con financiación ESTIMADA, nunca medida. El
            operador no puede declarar como verificado un coste que no se ha
            comprobado en el terminal.
        """
        clave = symbol.strip().upper()
        self.added[clave] = {
            "symbol": clave,
            "name": name.strip(),
            "yahoo_symbol": yahoo_symbol.strip(),
            "mt5_symbol": (mt5_symbol or "").strip() or None,
            "group": group.strip(),
            "currency": currency.strip().upper(),
        }
        self.disabled.discard(clave)
        self.save(path)

    def remove_added(self, symbol: str, path: Path | str = OVERLAY_PATH) -> None:
        """Elimina un instrumento que había añadido el operador."""
        self.added.pop(symbol.upper(), None)
        self.save(path)


def _entry_from_dict(datos: dict[str, Any]) -> base.CatalogEntry:
    """Construye una entrada de catálogo desde los datos del operador."""
    instrumento = CFD(
        symbol=datos["symbol"],
        name=datos["name"],
        currency=datos.get("currency", "USD"),
        tick_size=Decimal("0.01"),
        issuer=base.BROKER,
        underlying_ref=datos["name"],
        contract_multiplier=Decimal("1"),
        financing=FinancingTerms.estimated(
            note="Añadido por el operador; financiación sin verificar"
        ),
    )
    return base.CatalogEntry(
        instrument=instrumento,
        mt5_symbol=datos.get("mt5_symbol"),
        stooq_symbol=None,
        group=datos.get("group", "Otros"),
        core=False,
        yahoo_symbol=datos["yahoo_symbol"],
    )


def active_catalog(
    overlay: CatalogOverlay | None = None,
) -> tuple[base.CatalogEntry, ...]:
    """Catálogo efectivo: el base menos los desactivados, más los añadidos.

    Es lo que deben usar la ingesta, las señales y el panel, en lugar de
    `instruments.CATALOG` directamente.
    """
    capa = overlay or CatalogOverlay.load()
    activos = [e for e in base.CATALOG if e.symbol not in capa.disabled]
    activos.extend(
        _entry_from_dict(d) for s, d in capa.added.items() if s not in capa.disabled
    )
    return tuple(activos)


def full_catalog_status(
    overlay: CatalogOverlay | None = None,
) -> list[dict[str, Any]]:
    """Estado de todos los instrumentos, activos y desactivados.

    Alimenta la vista de gestión del catálogo en el panel: permite ver también
    los desactivados para poder reactivarlos.
    """
    capa = overlay or CatalogOverlay.load()
    filas: list[dict[str, Any]] = []

    for e in base.CATALOG:
        filas.append(
            {
                "symbol": e.symbol,
                "name": e.instrument.name,
                "group": e.group,
                "yahoo": e.yahoo_symbol,
                "mt5": e.mt5_symbol,
                "activo": e.symbol not in capa.disabled,
                "origen": "Catálogo base",
            }
        )

    for s, d in capa.added.items():
        filas.append(
            {
                "symbol": s,
                "name": d["name"],
                "group": d.get("group", "Otros"),
                "yahoo": d["yahoo_symbol"],
                "mt5": d.get("mt5_symbol"),
                "activo": s not in capa.disabled,
                "origen": "Añadido por el operador",
            }
        )

    return filas
