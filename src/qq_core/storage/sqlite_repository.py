"""Persistencia en SQLite.

POR QUÉ SQLITE Y NO TIMESCALEDB
--------------------------------
El diseño del Módulo 01 especifica TimescaleDB para producción. Esta
implementación NO lo sustituye: lo precede.

SQLite viene incluido en Python. No requiere servidor, ni Docker, ni
configuración. Eso permite tener el sistema funcionando con datos reales hoy,
mientras la infraestructura de producción se prepara en paralelo.

Y es la demostración práctica de que la arquitectura funciona: esta clase
implementa el mismo `BarRepository` que la versión de TimescaleDB. Cambiar de
una a otra es cambiar una línea en el punto de ensamblado. Ningún otro módulo
se entera.

Ambas implementaciones deben pasar la misma batería de pruebas de contrato.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterable

from qq_core.domain.bar import Bar
from qq_core.domain.enums import DataSource, Timeframe
from qq_core.domain.provenance import Provenance
from qq_core.storage.repository import UpsertReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS provenance (
    provenance_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    source            TEXT NOT NULL,
    provider_symbol   TEXT NOT NULL,
    ingested_at       TEXT NOT NULL,
    transform_version TEXT NOT NULL,
    raw_payload_hash  TEXT NOT NULL,
    request_params    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS bar (
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    source        TEXT NOT NULL,
    ts            TEXT NOT NULL,
    open          TEXT NOT NULL,
    high          TEXT NOT NULL,
    low           TEXT NOT NULL,
    close         TEXT NOT NULL,
    volume        TEXT,
    open_interest TEXT,
    content_hash  TEXT NOT NULL,
    provenance_id INTEGER NOT NULL REFERENCES provenance(provenance_id),
    first_seen_at TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    PRIMARY KEY (symbol, timeframe, source, ts)
);

CREATE INDEX IF NOT EXISTS bar_symbol_ts_idx ON bar (symbol, timeframe, ts);

-- Las revisiones del proveedor se registran, nunca se pisan en silencio.
CREATE TABLE IF NOT EXISTS bar_revision (
    symbol           TEXT NOT NULL,
    timeframe        TEXT NOT NULL,
    source           TEXT NOT NULL,
    ts               TEXT NOT NULL,
    detected_at      TEXT NOT NULL,
    old_content_hash TEXT NOT NULL,
    new_content_hash TEXT NOT NULL,
    old_values       TEXT NOT NULL,
    new_values       TEXT NOT NULL
);
"""

# Los precios se almacenan como TEXT, no REAL. SQLite no tiene tipo decimal:
# guardar 1.0350 como REAL introduce error binario que se acumula en el
# cálculo de PnL. TEXT preserva el valor exacto y se reconstruye a Decimal
# al leer.


class SQLiteBarRepository:
    """Repositorio de barras sobre SQLite. Mismo contrato que TimescaleDB.

    Args:
        path: Ruta del fichero de base de datos. `:memory:` para pruebas.
    """

    def __init__(self, path: str | Path = "qq_data.db") -> None:
        self.path = str(path)
        # `check_same_thread=False` permite que la conexión se use desde
        # varios hilos. Es necesario porque el panel crea un hilo nuevo por
        # cada interacción del usuario, y SQLite por defecto lo prohíbe.
        #
        # La seguridad se garantiza con el cerrojo: todas las operaciones que
        # tocan la conexión se serializan. SQLite soporta lecturas
        # concurrentes, pero una escritura simultánea desde dos hilos puede
        # corromper el estado de la transacción.
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

    def __enter__(self) -> "SQLiteBarRepository":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ------------------------------------------------------------------ #
    # Escritura
    # ------------------------------------------------------------------ #

    def upsert_bars(self, bars: Iterable[Bar], provenance: Provenance) -> UpsertReport:
        """Escribe barras de forma idempotente.

        Reescribir el mismo dato no modifica nada. Si el contenido cambió, se
        registra la revisión antes de sobrescribir.

        Args:
            bars: Barras a escribir.
            provenance: Procedencia común del lote.

        Returns:
            Conteo de insertadas, sin cambios y revisadas.
        """
        bars = list(bars)
        if not bars:
            return UpsertReport()

        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            cur = self._conn.cursor()
            cur.execute("BEGIN")
            return self._upsert(cur, bars, provenance, now)

    def _upsert(self, cur, bars, provenance, now) -> UpsertReport:
        """Cuerpo de la escritura. Se ejecuta siempre bajo el cerrojo."""
        try:
            cur.execute(
                """INSERT INTO provenance
                   (source, provider_symbol, ingested_at, transform_version,
                    raw_payload_hash, request_params)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    provenance.source.value,
                    provenance.provider_symbol,
                    provenance.ingested_at.isoformat(),
                    provenance.transform_version,
                    provenance.raw_payload_hash,
                    json.dumps(provenance.request_params, default=str),
                ),
            )
            prov_id = cur.lastrowid

            inserted = unchanged = revised = 0
            for bar in bars:
                key = (
                    bar.symbol,
                    bar.timeframe.value,
                    bar.source.value,
                    bar.ts.isoformat(),
                )
                row = cur.execute(
                    """SELECT content_hash, open, high, low, close, volume
                       FROM bar
                       WHERE symbol=? AND timeframe=? AND source=? AND ts=?""",
                    key,
                ).fetchone()
                new_hash = bar.content_hash

                if row is not None and row["content_hash"] == new_hash:
                    unchanged += 1
                    continue

                if row is not None:
                    cur.execute(
                        """INSERT INTO bar_revision
                           (symbol, timeframe, source, ts, detected_at,
                            old_content_hash, new_content_hash,
                            old_values, new_values)
                           VALUES (?,?,?,?,?,?,?,?,?)""",
                        (
                            *key,
                            now,
                            row["content_hash"],
                            new_hash,
                            json.dumps(dict(row), default=str),
                            json.dumps(_bar_values(bar), default=str),
                        ),
                    )
                    revised += 1
                else:
                    inserted += 1

                cur.execute(
                    """INSERT INTO bar
                       (symbol, timeframe, source, ts, open, high, low, close,
                        volume, open_interest, content_hash, provenance_id,
                        first_seen_at, updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(symbol, timeframe, source, ts) DO UPDATE SET
                         open=excluded.open, high=excluded.high,
                         low=excluded.low, close=excluded.close,
                         volume=excluded.volume,
                         open_interest=excluded.open_interest,
                         content_hash=excluded.content_hash,
                         provenance_id=excluded.provenance_id,
                         updated_at=excluded.updated_at""",
                    (
                        *key,
                        str(bar.open),
                        str(bar.high),
                        str(bar.low),
                        str(bar.close),
                        None if bar.volume is None else str(bar.volume),
                        None if bar.open_interest is None else str(bar.open_interest),
                        new_hash,
                        prov_id,
                        now,
                        now,
                    ),
                )
            cur.execute("COMMIT")
        except Exception:
            cur.execute("ROLLBACK")
            raise

        return UpsertReport(inserted=inserted, unchanged=unchanged, revised=revised)

    # ------------------------------------------------------------------ #
    # Lectura
    # ------------------------------------------------------------------ #

    def get_bars(
        self,
        symbol: str,
        timeframe: Timeframe,
        source: DataSource,
        start: datetime,
        end: datetime,
    ) -> list[Bar]:
        """Barras del rango [start, end), ordenadas por `ts`."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM bar
                   WHERE symbol=? AND timeframe=? AND source=? AND ts>=? AND ts<?
                   ORDER BY ts""",
                (
                    symbol,
                    timeframe.value,
                    source.value,
                    start.isoformat(),
                    end.isoformat(),
                ),
            ).fetchall()
        return [_row_to_bar(r) for r in rows]

    def get_watermark(
        self, symbol: str, timeframe: Timeframe, source: DataSource
    ) -> datetime | None:
        """Último `ts` almacenado para la serie, o `None` si está vacía."""
        with self._lock:
            row = self._conn.execute(
                """SELECT MAX(ts) AS m FROM bar
                   WHERE symbol=? AND timeframe=? AND source=?""",
                (symbol, timeframe.value, source.value),
            ).fetchone()
        return datetime.fromisoformat(row["m"]) if row and row["m"] else None

    # ------------------------------------------------------------------ #
    # Consultas de estado, usadas por el panel
    # ------------------------------------------------------------------ #

    def inventory(self) -> list[dict]:
        """Resumen de todas las series almacenadas.

        Returns:
            Una fila por serie con símbolo, resolución, fuente, número de
            barras y rango temporal cubierto.
        """
        with self._lock:
            rows = self._conn.execute(
                """SELECT symbol, timeframe, source,
                          COUNT(*) AS bars,
                          MIN(ts)  AS first_ts,
                          MAX(ts)  AS last_ts
                   FROM bar
                   GROUP BY symbol, timeframe, source
                   ORDER BY symbol"""
            ).fetchall()
        return [dict(r) for r in rows]

    def revision_count(self) -> int:
        """Número de revisiones del proveedor detectadas."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM bar_revision"
            ).fetchone()
        return int(row["n"])

    def primary_source(self) -> DataSource | None:
        """Fuente con más datos almacenados.

        El sistema puede tener series de varios proveedores conviviendo. Esta
        consulta permite que los consumidores usen la que más cobertura tiene
        sin tener que conocer de antemano qué proveedor se usó en la descarga.
        """
        with self._lock:
            row = self._conn.execute(
                """SELECT source, COUNT(*) AS n FROM bar
                   GROUP BY source ORDER BY n DESC LIMIT 1"""
            ).fetchone()
        return DataSource(row["source"]) if row else None

    def total_bars(self) -> int:
        """Total de barras almacenadas en el sistema."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM bar").fetchone()
        return int(row["n"])


def _bar_values(bar: Bar) -> dict:
    return {
        "open": str(bar.open),
        "high": str(bar.high),
        "low": str(bar.low),
        "close": str(bar.close),
        "volume": None if bar.volume is None else str(bar.volume),
    }


def _row_to_bar(row: sqlite3.Row) -> Bar:
    return Bar(
        symbol=row["symbol"],
        timeframe=Timeframe(row["timeframe"]),
        ts=datetime.fromisoformat(row["ts"]),
        source=DataSource(row["source"]),
        open=Decimal(row["open"]),
        high=Decimal(row["high"]),
        low=Decimal(row["low"]),
        close=Decimal(row["close"]),
        volume=None if row["volume"] is None else Decimal(row["volume"]),
        open_interest=(
            None if row["open_interest"] is None else Decimal(row["open_interest"])
        ),
    )
