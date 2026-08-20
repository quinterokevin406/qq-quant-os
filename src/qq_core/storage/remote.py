"""Almacenamiento permanente de los datos del operador en PostgreSQL.

POR QUÉ EXISTE ESTE MÓDULO
---------------------------
Streamlit Community Cloud borra el disco en cada reinicio. La copia automática
cubre los reinicios cortos, pero un despliegue nuevo también borra el
directorio temporal. Sólo una base de datos fuera del servidor resuelve el
problema por completo.

Con Supabase —gratuito— los datos del operador dejan de depender de dónde se
ejecute la aplicación. Se marca una señal y sigue ahí mañana, aunque la
aplicación se haya reiniciado veinte veces.

QUÉ SE GUARDA AQUÍ Y QUÉ NO
----------------------------
Sólo lo irreemplazable: seguimiento y operaciones ejecutadas. Los precios
siguen en SQLite local porque se regeneran descargándolos y almacenarlos en
remoto multiplicaría el coste y la latencia sin ganar nada.

Es la misma distinción que organiza todo el módulo de persistencia: datos del
operador frente a datos de mercado.

DEGRADACIÓN CONTROLADA
----------------------
Si la base de datos remota no está disponible, el sistema NO se cae: vuelve a
SQLite local y avisa. Una caída de Supabase no debe dejar al operador sin
poder consultar sus señales.
"""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timezone
from typing import Any

VAR_URL = "QQ_USER_DATA_URL"
"""Variable de entorno con la cadena de conexión de PostgreSQL."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_watch (
    watch_id        TEXT PRIMARY KEY,
    symbol          TEXT NOT NULL,
    strategy        TEXT NOT NULL,
    added_on        TEXT NOT NULL,
    signal_json     TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    active          INTEGER NOT NULL DEFAULT 1,
    closed_on       TEXT
);

CREATE TABLE IF NOT EXISTS real_trade (
    trade_id            TEXT PRIMARY KEY,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL,
    units               TEXT NOT NULL,
    entry_date          TEXT NOT NULL,
    entry_price         TEXT NOT NULL,
    exit_date           TEXT,
    exit_price          TEXT,
    stop_price          TEXT,
    target_price        TEXT,
    exit_reason         TEXT,
    strategy            TEXT NOT NULL DEFAULT '',
    signal_id           TEXT,
    signal_entry_price  TEXT,
    signal_date         TEXT,
    notes               TEXT NOT NULL DEFAULT '',
    broker_ticket       TEXT NOT NULL DEFAULT '',
    recorded_at         TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS real_trade_history (
    trade_id     TEXT NOT NULL,
    changed_at   TEXT NOT NULL,
    old_values   TEXT NOT NULL,
    PRIMARY KEY (trade_id, changed_at)
);
"""

# Los tipos siguen siendo TEXT, igual que en SQLite. Es deliberado: mantiene
# un único formato de serialización entre ambos motores, de modo que exportar
# de uno e importar en otro no requiere conversión.


def is_configured() -> bool:
    """Si hay una base de datos remota configurada."""
    return bool(os.environ.get(VAR_URL, "").strip())


def connection_url() -> str | None:
    """Cadena de conexión, si existe."""
    url = os.environ.get(VAR_URL, "").strip()
    return url or None


def masked_url() -> str:
    """Cadena de conexión con la contraseña oculta, para mostrarla.

    Nunca debe enseñarse la URL completa en la interfaz: contiene credenciales
    y quedaría visible en cualquier captura de pantalla.
    """
    url = connection_url()
    if not url:
        return "no configurada"
    try:
        antes, despues = url.split("://", 1)
        if "@" in despues:
            credenciales, servidor = despues.split("@", 1)
            usuario = credenciales.split(":", 1)[0]
            return f"{antes}://{usuario}:••••••@{servidor}"
    except ValueError:
        pass
    return "configurada"


class PostgresUserStore:
    """Almacén de datos del operador sobre PostgreSQL.

    Expone la misma interfaz que los almacenes locales, de modo que el resto
    del sistema no necesita saber cuál está en uso.

    Args:
        url: Cadena de conexión. Por defecto, la de la variable de entorno.

    Raises:
        RuntimeError: Si falta el controlador o la conexión no se establece.
    """

    def __init__(self, url: str | None = None) -> None:
        self._url = url or connection_url()
        if not self._url:
            raise RuntimeError(
                f"Falta la variable {VAR_URL} con la cadena de conexión."
            )
        try:
            import psycopg2  # noqa: F401
        except ImportError as exc:
            raise RuntimeError(
                "Falta el controlador de PostgreSQL. Instálalo con:\n"
                "    pip install psycopg2-binary"
            ) from exc

        self._lock = threading.RLock()
        self._conn = self._conectar()
        with self._lock, self._conn.cursor() as cursor:
            cursor.execute(SCHEMA)
        self._conn.commit()

    def _conectar(self):
        import psycopg2

        # `sslmode=require` es obligatorio en Supabase y en la mayoría de
        # servicios gestionados; sin él la conexión se rechaza.
        url = self._url
        if "sslmode=" not in url:
            separador = "&" if "?" in url else "?"
            url = f"{url}{separador}sslmode=require"
        return psycopg2.connect(url, connect_timeout=10)

    def _asegurar_conexion(self) -> None:
        """Reconecta si la conexión se ha cerrado.

        Los servicios gestionados cierran conexiones inactivas, y una
        aplicación que se consulta esporádicamente las pierde a menudo.
        """
        if self._conn.closed:
            self._conn = self._conectar()

    def close(self) -> None:
        with self._lock:
            if not self._conn.closed:
                self._conn.close()

    # ------------------------------------------------------------------ #
    # Operaciones genéricas
    # ------------------------------------------------------------------ #

    def replace_all(self, tabla: str, filas: list[dict[str, Any]]) -> int:
        """Sustituye el contenido de una tabla.

        Se usa al sincronizar desde el almacén local. La operación es atómica:
        o se escribe todo o no se escribe nada, de modo que una interrupción a
        media escritura no deja los datos a medias.
        """
        if tabla not in ("signal_watch", "real_trade"):
            raise ValueError(f"Tabla no permitida: {tabla}")

        with self._lock:
            self._asegurar_conexion()
            try:
                with self._conn.cursor() as cursor:
                    cursor.execute(f"DELETE FROM {tabla}")
                    escritas = 0
                    for fila in filas:
                        columnas = ", ".join(fila.keys())
                        marcadores = ", ".join("%s" for _ in fila)
                        cursor.execute(
                            f"INSERT INTO {tabla} ({columnas}) "
                            f"VALUES ({marcadores}) "
                            f"ON CONFLICT DO NOTHING",
                            tuple(_normalizar(v) for v in fila.values()),
                        )
                        escritas += 1
                self._conn.commit()
                return escritas
            except Exception:
                self._conn.rollback()
                raise

    def read_all(self, tabla: str) -> list[dict[str, Any]]:
        """Lee el contenido completo de una tabla."""
        if tabla not in ("signal_watch", "real_trade"):
            raise ValueError(f"Tabla no permitida: {tabla}")

        with self._lock:
            self._asegurar_conexion()
            with self._conn.cursor() as cursor:
                cursor.execute(f"SELECT * FROM {tabla}")
                columnas = [d[0] for d in cursor.description]
                return [dict(zip(columnas, fila)) for fila in cursor.fetchall()]

    def counts(self) -> dict[str, int]:
        """Número de registros por tabla, para mostrarlo en el panel."""
        with self._lock:
            self._asegurar_conexion()
            with self._conn.cursor() as cursor:
                resultado = {}
                for tabla in ("signal_watch", "real_trade"):
                    cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
                    resultado[tabla] = int(cursor.fetchone()[0])
                return resultado


def _normalizar(valor: Any) -> Any:
    """Convierte tipos de Python a lo que espera el controlador."""
    if isinstance(valor, (datetime, date)):
        return valor.isoformat()
    if isinstance(valor, (dict, list)):
        return json.dumps(valor, default=str)
    return valor


# --------------------------------------------------------------------- #
# Sincronización con el almacén local
# --------------------------------------------------------------------- #


def push_to_remote(db_path) -> dict[str, int] | None:
    """Envía los datos locales a la base remota.

    Se llama tras cada cambio del operador. La base remota pasa a ser la
    copia autoritativa.

    Returns:
        Recuento de lo enviado, o `None` si no hay base remota configurada.
    """
    if not is_configured():
        return None

    from qq_core.storage.user_data import export_user_data

    copia = export_user_data(db_path)
    almacen = PostgresUserStore()
    try:
        return {
            "seguimiento": almacen.replace_all("signal_watch", copia.watchlist),
            "operaciones": almacen.replace_all("real_trade", copia.trades),
        }
    finally:
        almacen.close()


def pull_from_remote(db_path) -> dict[str, int] | None:
    """Trae los datos de la base remota al almacén local.

    Se llama al arrancar. Después de un reinicio, el disco local está vacío y
    la base remota conserva todo: esta función es la que devuelve al operador
    su seguimiento.

    Returns:
        Recuento de lo recuperado, o `None` si no hay base remota.
    """
    if not is_configured():
        return None

    from qq_core.storage.user_data import (
        FORMATO_EXPORT,
        UserDataSnapshot,
        import_user_data,
    )

    almacen = PostgresUserStore()
    try:
        copia = UserDataSnapshot(
            version=FORMATO_EXPORT,
            exported_at=datetime.now(timezone.utc),
            watchlist=almacen.read_all("signal_watch"),
            trades=almacen.read_all("real_trade"),
        )
    finally:
        almacen.close()

    if not copia.watchlist and not copia.trades:
        return None

    return import_user_data(db_path, copia, replace=True)


def remote_status() -> dict[str, Any]:
    """Estado de la conexión remota, para el panel.

    Nunca lanza excepción: un fallo de conexión debe informarse, no tumbar la
    aplicación.
    """
    if not is_configured():
        return {"configurada": False, "conectada": False, "url": "no configurada"}

    try:
        almacen = PostgresUserStore()
        try:
            conteos = almacen.counts()
        finally:
            almacen.close()
        return {
            "configurada": True,
            "conectada": True,
            "url": masked_url(),
            "seguimiento": conteos["signal_watch"],
            "operaciones": conteos["real_trade"],
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "configurada": True,
            "conectada": False,
            "url": masked_url(),
            "error": str(exc)[:200],
        }
