"""Persistencia de los datos del operador (Módulo 16).

EL PROBLEMA QUE RESUELVE
------------------------
Streamlit Community Cloud borra el sistema de archivos cada vez que la
aplicación se reinicia, se despliega de nuevo o despierta tras dormirse. Todo
lo escrito en disco desaparece.

Para los precios eso no importa: se vuelven a descargar. Pero las señales
marcadas para seguimiento y las operaciones registradas son datos del
operador, irrepetibles. Perderlos hace inútil la función.

Fue un error de diseño: se guardaron datos irreemplazables en el mismo
almacenamiento temporal que los datos regenerables.

DISTINCIÓN QUE ORGANIZA TODO EL MÓDULO
---------------------------------------
**Datos regenerables**: precios de mercado. Si se pierden, se descargan otra
vez. Pueden vivir en almacenamiento temporal.

**Datos del operador**: señales en seguimiento, operaciones ejecutadas, notas.
Sólo existen aquí. Necesitan almacenamiento que sobreviva a los reinicios.

TRES NIVELES DE PERSISTENCIA
-----------------------------
1. **Fichero local** — para uso en el ordenador propio. Persiste mientras no
   se borre la carpeta.
2. **Base de datos externa** — configurando `QQ_USER_DATA_URL` con una URL de
   PostgreSQL. Es la solución correcta para el despliegue en la nube.
3. **Exportación manual** — descargar un fichero y volver a subirlo. Funciona
   siempre, sin depender de nada, y sirve además como copia de seguridad.

El sistema usa el nivel más alto disponible y avisa cuando está en uno que no
garantiza la persistencia.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

FORMATO_EXPORT = "qq-quant-os/user-data/1"
"""Identificador del formato de exportación. Permite detectar ficheros de
versiones futuras en lugar de interpretarlos mal."""

VAR_URL_EXTERNA = "QQ_USER_DATA_URL"
"""Variable de entorno con la URL de la base de datos externa."""


class Persistence(StrEnum):
    """Nivel de persistencia en uso."""

    EXTERNAL = "externa"
    """Base de datos remota. Sobrevive a cualquier reinicio."""

    LOCAL = "local"
    """Fichero en disco. Sobrevive mientras no se borre la carpeta."""

    EPHEMERAL = "temporal"
    """Almacenamiento que se pierde al reiniciar. Requiere exportar a mano."""

    @property
    def is_durable(self) -> bool:
        return self is not Persistence.EPHEMERAL

    @property
    def warning(self) -> str | None:
        """Aviso para el operador, cuando corresponde."""
        if self is Persistence.EPHEMERAL:
            return (
                "**Los datos de seguimiento no se conservan.** Esta instalación "
                "usa almacenamiento temporal: al reiniciarse la aplicación se "
                "pierden las señales marcadas y las operaciones registradas. "
                "Descarga tu copia antes de cerrar, o configura una base de "
                "datos externa."
            )
        if self is Persistence.LOCAL:
            return (
                "Los datos se guardan en el ordenador donde corre la "
                "aplicación. Descarga una copia periódicamente."
            )
        return None


def detect_persistence(db_path: Path | str) -> Persistence:
    """Determina qué nivel de persistencia ofrece el entorno actual.

    Args:
        db_path: Ruta de la base de datos local.

    Returns:
        Nivel detectado.

    Note:
        Streamlit Community Cloud monta la aplicación bajo `/mount/src`. Es la
        señal más fiable de que el almacenamiento es temporal, porque el
        contenedor se recrea en cada reinicio.
    """
    if os.environ.get(VAR_URL_EXTERNA):
        return Persistence.EXTERNAL

    ruta = str(Path(db_path).resolve())
    if ruta.startswith("/mount/src") or os.environ.get("STREAMLIT_SERVER_HEADLESS"):
        # En la nube, aunque el fichero exista ahora, desaparecerá.
        if not os.environ.get("QQ_FORCE_LOCAL_PERSISTENCE"):
            return Persistence.EPHEMERAL
    return Persistence.LOCAL


@dataclass
class UserDataSnapshot:
    """Copia completa de los datos del operador.

    Es lo que se descarga y se vuelve a subir. Contiene todo lo que no puede
    regenerarse: seguimiento y operaciones. Los precios quedan fuera a
    propósito, porque volverían a descargarse igualmente y multiplicarían el
    tamaño del fichero.
    """

    version: str
    exported_at: datetime
    watchlist: list[dict[str, Any]]
    trades: list[dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps(
            {
                "formato": self.version,
                "exportado": self.exported_at.isoformat(),
                "seguimiento": self.watchlist,
                "operaciones": self.trades,
            },
            indent=2,
            ensure_ascii=False,
            default=str,
        )

    @classmethod
    def from_json(cls, contenido: str | bytes) -> "UserDataSnapshot":
        """Reconstruye una copia desde su fichero.

        Raises:
            ValueError: Si el fichero no tiene el formato esperado. Se prefiere
                fallar a importar datos que podrían estar corruptos.
        """
        if isinstance(contenido, bytes):
            contenido = contenido.decode("utf-8")
        try:
            datos = json.loads(contenido)
        except json.JSONDecodeError as exc:
            raise ValueError(f"El fichero no es un JSON válido: {exc}") from exc

        formato = datos.get("formato", "")
        if not formato.startswith("qq-quant-os/user-data/"):
            raise ValueError(
                "El fichero no parece una copia de QQ Quant OS. Comprueba que "
                "es el archivo descargado desde la propia aplicación."
            )

        return cls(
            version=formato,
            exported_at=datetime.fromisoformat(
                datos.get("exportado", datetime.now(timezone.utc).isoformat())
            ),
            watchlist=datos.get("seguimiento", []),
            trades=datos.get("operaciones", []),
        )

    @property
    def summary(self) -> str:
        return (
            f"{len(self.watchlist)} señal(es) en seguimiento y "
            f"{len(self.trades)} operación(es), exportadas el "
            f"{self.exported_at.date().isoformat()}"
        )


def export_user_data(db_path: Path | str) -> UserDataSnapshot:
    """Extrae todos los datos del operador para su descarga.

    Args:
        db_path: Base de datos de la que leer.

    Returns:
        Copia lista para serializar.
    """
    import sqlite3

    conexion = sqlite3.connect(str(db_path))
    conexion.row_factory = sqlite3.Row

    def _leer(tabla: str) -> list[dict[str, Any]]:
        try:
            return [dict(f) for f in conexion.execute(f"SELECT * FROM {tabla}")]
        except sqlite3.OperationalError:
            # La tabla puede no existir todavía si nunca se usó la función.
            return []

    seguimiento = _leer("signal_watch")
    operaciones = _leer("real_trade")
    conexion.close()

    return UserDataSnapshot(
        version=FORMATO_EXPORT,
        exported_at=datetime.now(timezone.utc),
        watchlist=seguimiento,
        trades=operaciones,
    )


def import_user_data(
    db_path: Path | str, snapshot: UserDataSnapshot, replace: bool = False
) -> dict[str, int]:
    """Restaura los datos del operador desde una copia.

    Args:
        db_path: Base de datos de destino.
        snapshot: Copia a restaurar.
        replace: Si `True`, sustituye lo existente. Si `False`, fusiona
            conservando lo que ya hubiera.

    Returns:
        Recuento de registros restaurados por tipo.

    Note:
        La fusión usa `INSERT OR REPLACE` sobre la clave primaria. Restaurar
        dos veces la misma copia no duplica nada, lo que permite usarla sin
        miedo tras un reinicio.
    """
    import sqlite3

    from qq_core.execution.journal import SCHEMA as SCHEMA_OPERACIONES
    from qq_core.signals.watchlist import SCHEMA as SCHEMA_SEGUIMIENTO

    conexion = sqlite3.connect(str(db_path), isolation_level=None)
    conexion.executescript(SCHEMA_SEGUIMIENTO)
    conexion.executescript(SCHEMA_OPERACIONES)

    if replace:
        conexion.execute("DELETE FROM signal_watch")
        conexion.execute("DELETE FROM real_trade")

    restaurados = {"seguimiento": 0, "operaciones": 0}

    for fila in snapshot.watchlist:
        columnas = ", ".join(fila.keys())
        marcadores = ", ".join("?" for _ in fila)
        try:
            conexion.execute(
                f"INSERT OR REPLACE INTO signal_watch ({columnas}) "
                f"VALUES ({marcadores})",
                tuple(fila.values()),
            )
            restaurados["seguimiento"] += 1
        except sqlite3.Error:
            continue

    for fila in snapshot.trades:
        columnas = ", ".join(fila.keys())
        marcadores = ", ".join("?" for _ in fila)
        try:
            conexion.execute(
                f"INSERT OR REPLACE INTO real_trade ({columnas}) "
                f"VALUES ({marcadores})",
                tuple(fila.values()),
            )
            restaurados["operaciones"] += 1
        except sqlite3.Error:
            continue

    conexion.close()
    return restaurados


def suggested_filename(prefix: str = "qq-quant-datos") -> str:
    """Nombre de fichero para la descarga, con la fecha incorporada."""
    return f"{prefix}-{date.today().isoformat()}.json"
