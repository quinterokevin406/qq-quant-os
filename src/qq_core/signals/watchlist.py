"""Lista de seguimiento: señales que el operador está vigilando.

QUÉ RESUELVE
------------
El operador toma una posición sobre una señal y quiere saber, cada día, si la
proyección sigue siendo la misma. No necesita registrar precio ni unidades
—eso es el diario de operaciones reales—: le basta con marcar «estoy dentro de
esta» y recibir el seguimiento.

Es deliberadamente más ligero que el diario. Marcar una señal cuesta un clic;
registrar una operación completa exige precio, unidades y ticket. Si lo único
que se ofreciera fuese lo segundo, el seguimiento no se usaría.

QUÉ SE GUARDA
-------------
La señal tal como estaba el día que se marcó: dirección, convicción, precio,
objetivo e invalidación. Ese es el punto de referencia contra el que se
compara la señal de hoy. Guardar sólo el símbolo no serviría: sin saber cómo
era la señal original no se puede decir si se ha debilitado.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from qq_core.domain.signal import (
    Direction,
    Horizon,
    Signal,
    SignalStrength,
)

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

CREATE INDEX IF NOT EXISTS signal_watch_active_idx ON signal_watch (active);
"""


def watch_id(symbol: str, strategy: str, added: date) -> str:
    """Identificador de una entrada en seguimiento.

    Incluye la fecha para que se pueda seguir el mismo instrumento y
    estrategia en dos momentos distintos sin que uno pise al otro.
    """
    return f"{symbol}:{strategy}:{added.isoformat()}"


class Watchlist:
    """Señales bajo seguimiento del operador.

    Args:
        path: Base de datos. Comparte fichero con precios y operaciones.
    """

    def __init__(self, path: str | Path = "qq_data.db") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(
            self.path, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ #
    # Escritura
    # ------------------------------------------------------------------ #

    def add(self, signal: Signal, note: str = "", added: date | None = None) -> str:
        """Marca una señal para seguimiento diario.

        Args:
            signal: Señal tal como está hoy. Se guarda íntegra como punto de
                referencia.
            note: Comentario del operador.
            added: Fecha de alta. Por defecto, hoy.

        Returns:
            Identificador de la entrada creada.
        """
        fecha = added or date.today()
        identificador = watch_id(signal.symbol, signal.strategy, fecha)

        with self._lock:
            self._conn.execute(
                """INSERT INTO signal_watch
                   (watch_id, symbol, strategy, added_on, signal_json, note, active)
                   VALUES (?,?,?,?,?,?,1)
                   ON CONFLICT(watch_id) DO UPDATE SET
                       signal_json=excluded.signal_json,
                       note=excluded.note, active=1, closed_on=NULL""",
                (
                    identificador, signal.symbol, signal.strategy,
                    fecha.isoformat(), signal.model_dump_json(), note,
                ),
            )
        return identificador

    def remove(self, identificador: str) -> None:
        """Retira una señal del seguimiento.

        No se borra: se marca como inactiva. El histórico de qué se siguió y
        durante cuánto tiempo forma parte del registro de decisiones.
        """
        with self._lock:
            self._conn.execute(
                "UPDATE signal_watch SET active=0, closed_on=? WHERE watch_id=?",
                (date.today().isoformat(), identificador),
            )

    # ------------------------------------------------------------------ #
    # Lectura
    # ------------------------------------------------------------------ #

    def active_entries(self) -> list[tuple[str, Signal, str, date]]:
        """Señales en seguimiento activo.

        Returns:
            Lista de `(identificador, señal_original, nota, fecha_alta)`.
        """
        with self._lock:
            filas = self._conn.execute(
                "SELECT * FROM signal_watch WHERE active=1 ORDER BY added_on DESC"
            ).fetchall()

        salida = []
        for f in filas:
            try:
                señal = Signal.model_validate_json(f["signal_json"])
            except Exception:  # noqa: BLE001
                # Una entrada guardada con un esquema anterior no debe impedir
                # que el resto del seguimiento funcione.
                continue
            salida.append(
                (f["watch_id"], señal, f["note"] or "",
                 date.fromisoformat(f["added_on"]))
            )
        return salida

    def is_watched(self, symbol: str, strategy: str) -> bool:
        """Si ya se está siguiendo esta combinación."""
        with self._lock:
            fila = self._conn.execute(
                "SELECT 1 FROM signal_watch WHERE symbol=? AND strategy=? "
                "AND active=1 LIMIT 1",
                (symbol, strategy),
            ).fetchone()
        return fila is not None

    def count(self) -> int:
        with self._lock:
            fila = self._conn.execute(
                "SELECT COUNT(*) AS n FROM signal_watch WHERE active=1"
            ).fetchone()
        return int(fila["n"])
