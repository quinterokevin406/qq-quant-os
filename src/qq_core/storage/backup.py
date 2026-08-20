"""Copia de seguridad automática de los datos del operador.

POR QUÉ NO BASTA CON PODER EXPORTAR
------------------------------------
Una función de exportación manual sólo protege a quien se acuerda de usarla.
Y el momento en que se pierden los datos —un reinicio de la aplicación— llega
sin aviso.

Este módulo guarda una copia automática cada vez que el operador modifica algo,
y la restaura sola al arrancar si detecta que la base de datos está vacía pero
existe una copia. El operador no tiene que hacer nada.

DÓNDE SE GUARDA LA COPIA
-------------------------
En el directorio que indique `QQ_BACKUP_DIR`, o en el directorio temporal del
sistema. En un ordenador propio eso ya sobrevive a reinicios de la aplicación.

En la nube, el directorio temporal también se borra al recrearse el contenedor,
así que la copia automática cubre los reinicios cortos pero no los
despliegues. Para eso está la exportación manual, y para resolverlo del todo,
una base de datos externa.

Se declara la limitación en lugar de dar una falsa sensación de seguridad.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from qq_core.storage.user_data import (
    UserDataSnapshot,
    export_user_data,
    import_user_data,
)

VAR_DIRECTORIO = "QQ_BACKUP_DIR"
NOMBRE_COPIA = "qq_user_data_backup.json"
COPIAS_CONSERVADAS = 5
"""Copias anteriores que se conservan.

Existen porque una copia automática puede grabar un estado ya dañado: si el
operador borra algo por error y la copia se sobrescribe, no habría vuelta
atrás. Con varias generaciones sí la hay.
"""


def backup_dir() -> Path:
    """Directorio donde se guardan las copias."""
    configurado = os.environ.get(VAR_DIRECTORIO)
    ruta = Path(configurado) if configurado else Path(tempfile.gettempdir()) / "qq_quant"
    ruta.mkdir(parents=True, exist_ok=True)
    return ruta


def backup_path() -> Path:
    """Ruta de la copia más reciente."""
    return backup_dir() / NOMBRE_COPIA


def save_backup(db_path: Path | str) -> Path | None:
    """Guarda una copia de los datos del operador.

    Rota las copias anteriores antes de escribir la nueva, de modo que un
    borrado accidental pueda deshacerse.

    Args:
        db_path: Base de datos de la que copiar.

    Returns:
        Ruta de la copia escrita, o `None` si no había nada que copiar.
    """
    copia = export_user_data(db_path)
    if not copia.watchlist and not copia.trades:
        # No se sobrescribe una copia buena con uno vacía: si la base de datos
        # se ha perdido, la copia anterior es justamente lo que hay que salvar.
        return None

    destino = backup_path()
    _rotar(destino)
    destino.write_text(copia.to_json(), encoding="utf-8")
    return destino


def _rotar(destino: Path) -> None:
    """Conserva las generaciones anteriores antes de sobrescribir."""
    if not destino.exists():
        return
    for i in range(COPIAS_CONSERVADAS - 1, 0, -1):
        anterior = destino.with_suffix(f".{i}.json")
        siguiente = destino.with_suffix(f".{i + 1}.json")
        if anterior.exists():
            shutil.move(str(anterior), str(siguiente))
    shutil.copy2(str(destino), str(destino.with_suffix(".1.json")))


def load_backup() -> UserDataSnapshot | None:
    """Lee la copia más reciente, si existe y es legible."""
    ruta = backup_path()
    if not ruta.exists():
        return None
    try:
        return UserDataSnapshot.from_json(ruta.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def restore_if_empty(db_path: Path | str) -> dict[str, int] | None:
    """Restaura la copia automática si la base de datos está vacía.

    Es la función que hace que el operador no pierda su seguimiento tras un
    reinicio: se ejecuta al arrancar y sólo actúa si detecta que no hay datos
    pero sí una copia.

    Deliberadamente NO restaura cuando ya hay datos: sobrescribir lo que el
    operador tiene ahora con una copia antigua sería peor que no hacer nada.

    Args:
        db_path: Base de datos a comprobar.

    Returns:
        Recuento de lo restaurado, o `None` si no hizo falta.
    """
    actual = export_user_data(db_path)
    if actual.watchlist or actual.trades:
        return None

    copia = load_backup()
    if copia is None or (not copia.watchlist and not copia.trades):
        return None

    return import_user_data(db_path, copia, replace=False)


def backup_info() -> dict[str, object]:
    """Estado de la copia automática, para mostrarlo en el panel."""
    ruta = backup_path()
    if not ruta.exists():
        return {"existe": False, "directorio": str(backup_dir())}

    copia = load_backup()
    return {
        "existe": True,
        "directorio": str(backup_dir()),
        "fecha": (
            copia.exported_at.astimezone(timezone.utc) if copia else None
        ),
        "seguimiento": len(copia.watchlist) if copia else 0,
        "operaciones": len(copia.trades) if copia else 0,
        "generaciones": sum(
            1 for i in range(1, COPIAS_CONSERVADAS + 1)
            if ruta.with_suffix(f".{i}.json").exists()
        ),
    }
