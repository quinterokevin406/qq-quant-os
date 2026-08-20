"""Diario de operaciones: almacenamiento y análisis (Módulo 13).

ALMACENAMIENTO SEPARADO DE LOS PRECIOS
---------------------------------------
Las operaciones se guardan en su propia tabla, no mezcladas con las series de
precio. Razón: los precios son datos externos reemplazables —si se corrompen se
vuelven a descargar—, mientras que el registro de operaciones es información
que sólo existe aquí. Perderlo es perder el historial de decisiones de la
empresa.

Por eso nada en este módulo borra registros. Corregir una operación crea una
versión nueva; el original queda.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from qq_core.domain.signal import Direction
from qq_core.execution.trade import ExitReason, RealTrade

SCHEMA = """
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

CREATE INDEX IF NOT EXISTS real_trade_symbol_idx ON real_trade (symbol);
CREATE INDEX IF NOT EXISTS real_trade_open_idx ON real_trade (exit_date);

-- Historial de modificaciones. Corregir un dato no borra el anterior: en un
-- registro contable, saber qué se cambió y cuándo forma parte del registro.
CREATE TABLE IF NOT EXISTS real_trade_history (
    trade_id     TEXT NOT NULL,
    changed_at   TEXT NOT NULL,
    old_values   TEXT NOT NULL,
    PRIMARY KEY (trade_id, changed_at)
);
"""

# Los precios se guardan como TEXT por la misma razón que en las series: SQLite
# no tiene tipo decimal y almacenarlos como número introduce error binario que
# se acumula al sumar resultados.


class TradeJournal:
    """Diario de operaciones reales.

    Args:
        path: Fichero de base de datos. Puede ser el mismo que el de precios;
            las tablas son independientes.
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

    def record(self, trade: RealTrade) -> None:
        """Registra una operación, o actualiza una existente.

        Si el identificador ya existe, guarda los valores anteriores en el
        historial antes de sobrescribir. Así una corrección queda trazada.
        """
        ahora = datetime.now(timezone.utc).isoformat()
        with self._lock:
            existente = self._conn.execute(
                "SELECT * FROM real_trade WHERE trade_id = ?", (trade.trade_id,)
            ).fetchone()

            if existente is not None:
                self._conn.execute(
                    """INSERT OR REPLACE INTO real_trade_history
                       (trade_id, changed_at, old_values) VALUES (?, ?, ?)""",
                    (trade.trade_id, ahora, json.dumps(dict(existente), default=str)),
                )

            self._conn.execute(
                """INSERT INTO real_trade (
                       trade_id, symbol, direction, units, entry_date, entry_price,
                       exit_date, exit_price, stop_price, target_price, exit_reason,
                       strategy, signal_id, signal_entry_price, signal_date,
                       notes, broker_ticket, recorded_at, updated_at
                   ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(trade_id) DO UPDATE SET
                       symbol=excluded.symbol, direction=excluded.direction,
                       units=excluded.units, entry_date=excluded.entry_date,
                       entry_price=excluded.entry_price,
                       exit_date=excluded.exit_date, exit_price=excluded.exit_price,
                       stop_price=excluded.stop_price,
                       target_price=excluded.target_price,
                       exit_reason=excluded.exit_reason, strategy=excluded.strategy,
                       signal_id=excluded.signal_id,
                       signal_entry_price=excluded.signal_entry_price,
                       signal_date=excluded.signal_date, notes=excluded.notes,
                       broker_ticket=excluded.broker_ticket,
                       updated_at=excluded.updated_at""",
                (
                    trade.trade_id, trade.symbol, trade.direction.value,
                    str(trade.units), trade.entry_date.isoformat(),
                    str(trade.entry_price),
                    trade.exit_date.isoformat() if trade.exit_date else None,
                    str(trade.exit_price) if trade.exit_price else None,
                    str(trade.stop_price) if trade.stop_price else None,
                    str(trade.target_price) if trade.target_price else None,
                    trade.exit_reason.value if trade.exit_reason else None,
                    trade.strategy, trade.signal_id,
                    str(trade.signal_entry_price) if trade.signal_entry_price else None,
                    trade.signal_date.isoformat() if trade.signal_date else None,
                    trade.notes, trade.broker_ticket,
                    trade.recorded_at.isoformat(), ahora,
                ),
            )

    def close_trade(
        self,
        trade_id: str,
        exit_date: date,
        exit_price: Decimal,
        reason: ExitReason = ExitReason.MANUAL,
        notes: str = "",
    ) -> RealTrade | None:
        """Cierra una posición abierta.

        Returns:
            La operación actualizada, o `None` si no existe.
        """
        operacion = self.get(trade_id)
        if operacion is None:
            return None
        actualizada = operacion.model_copy(
            update={
                "exit_date": exit_date,
                "exit_price": exit_price,
                "exit_reason": reason,
                "notes": notes or operacion.notes,
            }
        )
        self.record(actualizada)
        return actualizada

    def delete(self, trade_id: str) -> None:
        """Elimina un registro introducido por error.

        Se conserva en el historial: el borrado es del registro activo, no de
        la evidencia de que existió.
        """
        with self._lock:
            fila = self._conn.execute(
                "SELECT * FROM real_trade WHERE trade_id = ?", (trade_id,)
            ).fetchone()
            if fila is None:
                return
            self._conn.execute(
                """INSERT OR REPLACE INTO real_trade_history
                   (trade_id, changed_at, old_values) VALUES (?, ?, ?)""",
                (
                    trade_id,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps({**dict(fila), "_borrado": True}, default=str),
                ),
            )
            self._conn.execute(
                "DELETE FROM real_trade WHERE trade_id = ?", (trade_id,)
            )

    # ------------------------------------------------------------------ #
    # Lectura
    # ------------------------------------------------------------------ #

    def get(self, trade_id: str) -> RealTrade | None:
        with self._lock:
            fila = self._conn.execute(
                "SELECT * FROM real_trade WHERE trade_id = ?", (trade_id,)
            ).fetchone()
        return _row_to_trade(fila) if fila else None

    def all_trades(self) -> list[RealTrade]:
        """Todas las operaciones, las más recientes primero."""
        with self._lock:
            filas = self._conn.execute(
                "SELECT * FROM real_trade ORDER BY entry_date DESC"
            ).fetchall()
        return [_row_to_trade(f) for f in filas]

    def open_trades(self) -> list[RealTrade]:
        with self._lock:
            filas = self._conn.execute(
                "SELECT * FROM real_trade WHERE exit_date IS NULL "
                "ORDER BY entry_date DESC"
            ).fetchall()
        return [_row_to_trade(f) for f in filas]

    def closed_trades(self) -> list[RealTrade]:
        with self._lock:
            filas = self._conn.execute(
                "SELECT * FROM real_trade WHERE exit_date IS NOT NULL "
                "ORDER BY exit_date DESC"
            ).fetchall()
        return [_row_to_trade(f) for f in filas]

    def count(self) -> int:
        with self._lock:
            fila = self._conn.execute(
                "SELECT COUNT(*) AS n FROM real_trade"
            ).fetchone()
        return int(fila["n"])


def _row_to_trade(row: sqlite3.Row) -> RealTrade:
    def dec(valor) -> Decimal | None:
        return Decimal(valor) if valor is not None else None

    def fecha(valor) -> date | None:
        return date.fromisoformat(valor) if valor else None

    return RealTrade(
        trade_id=row["trade_id"],
        symbol=row["symbol"],
        direction=Direction(row["direction"]),
        units=Decimal(row["units"]),
        entry_date=date.fromisoformat(row["entry_date"]),
        entry_price=Decimal(row["entry_price"]),
        exit_date=fecha(row["exit_date"]),
        exit_price=dec(row["exit_price"]),
        stop_price=dec(row["stop_price"]),
        target_price=dec(row["target_price"]),
        exit_reason=ExitReason(row["exit_reason"]) if row["exit_reason"] else None,
        strategy=row["strategy"] or "",
        signal_id=row["signal_id"],
        signal_entry_price=dec(row["signal_entry_price"]),
        signal_date=fecha(row["signal_date"]),
        notes=row["notes"] or "",
        broker_ticket=row["broker_ticket"] or "",
        recorded_at=datetime.fromisoformat(row["recorded_at"]),
    )


# --------------------------------------------------------------------- #
# Analítica de la cuenta real
# --------------------------------------------------------------------- #


def account_summary(
    trades: list[RealTrade],
    initial_capital: Decimal,
    current_prices: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Estado de la cuenta a partir de las operaciones registradas.

    Args:
        trades: Operaciones del diario.
        initial_capital: Capital con el que se empezó.
        current_prices: Precios actuales, para valorar posiciones abiertas.

    Returns:
        Métricas de la cuenta real.
    """
    precios = current_prices or {}
    cerradas = [t for t in trades if not t.is_open]
    abiertas = [t for t in trades if t.is_open]

    realizado = sum((t.pnl() for t in cerradas), start=Decimal(0))
    no_realizado = sum(
        (t.pnl(precios.get(t.symbol)) for t in abiertas), start=Decimal(0)
    )
    capital = initial_capital + realizado + no_realizado

    ganadoras = [t for t in cerradas if t.pnl() > 0]
    perdedoras = [t for t in cerradas if t.pnl() <= 0]
    suma_perdidas = sum((t.pnl() for t in perdedoras), start=Decimal(0))

    return {
        "capital_inicial": float(initial_capital),
        "capital_actual": float(capital),
        "resultado_realizado": float(realizado),
        "resultado_no_realizado": float(no_realizado),
        "rentabilidad_pct": (
            float((capital / initial_capital - 1) * 100) if initial_capital else 0.0
        ),
        "operaciones_totales": len(trades),
        "operaciones_abiertas": len(abiertas),
        "operaciones_cerradas": len(cerradas),
        "aciertos_pct": (len(ganadoras) / len(cerradas) * 100) if cerradas else 0.0,
        "ganancia_media": (
            float(sum((t.pnl() for t in ganadoras), start=Decimal(0)) / len(ganadoras))
            if ganadoras else 0.0
        ),
        "perdida_media": (
            float(suma_perdidas / len(perdedoras)) if perdedoras else 0.0
        ),
        "factor_beneficio": (
            float(abs(sum((t.pnl() for t in ganadoras), start=Decimal(0)) / suma_perdidas))
            if perdedoras and suma_perdidas != 0 else 0.0
        ),
        "exposicion_abierta": float(sum((t.notional for t in abiertas), start=Decimal(0))),
        "dias_medios": (
            sum(t.days_held for t in cerradas) / len(cerradas) if cerradas else 0.0
        ),
    }


def execution_quality(trades: list[RealTrade]) -> dict[str, Any]:
    """Calidad de la ejecución manual: la brecha entre señal y realidad.

    Es la métrica propia de este módulo y la razón principal de que exista.
    Mide tres cosas que un sistema automático no puede observar:

      1. Cuántas operaciones siguieron una señal y cuántas fueron discrecionales.
      2. Cuánto se pierde por entrar a peor precio del recomendado.
      3. Cuánto se tarda en ejecutar desde que la señal se genera.

    Args:
        trades: Operaciones del diario.

    Returns:
        Métricas de calidad de ejecución, o un diccionario con `sin_datos` si
        no hay operaciones suficientes.
    """
    if not trades:
        return {"sin_datos": True}

    con_senal = [t for t in trades if t.followed_signal]
    sin_senal = [t for t in trades if not t.followed_signal]

    deslizamientos = [
        t.slippage_pct for t in trades if t.slippage_pct is not None
    ]
    retrasos = [
        t.decision_lag_days for t in trades if t.decision_lag_days is not None
    ]
    disciplina = [
        t.respected_stop for t in trades if t.respected_stop is not None
    ]

    cerradas_con = [t for t in con_senal if not t.is_open]
    cerradas_sin = [t for t in sin_senal if not t.is_open]

    return {
        "sin_datos": False,
        "operaciones_con_senal": len(con_senal),
        "operaciones_discrecionales": len(sin_senal),
        "pct_con_senal": len(con_senal) / len(trades) * 100,
        "deslizamiento_medio_pct": (
            sum(deslizamientos) / len(deslizamientos) if deslizamientos else None
        ),
        "deslizamiento_peor_pct": max(deslizamientos) if deslizamientos else None,
        "retraso_medio_dias": (
            sum(retrasos) / len(retrasos) if retrasos else None
        ),
        "retraso_maximo_dias": max(retrasos) if retrasos else None,
        "disciplina_stop_pct": (
            sum(1 for d in disciplina if d) / len(disciplina) * 100
            if disciplina else None
        ),
        "resultado_con_senal": float(
            sum((t.pnl() for t in cerradas_con), start=Decimal(0))
        ),
        "resultado_discrecional": float(
            sum((t.pnl() for t in cerradas_sin), start=Decimal(0))
        ),
    }


def strategy_performance(trades: list[RealTrade]) -> list[dict[str, Any]]:
    """Resultado real por estrategia.

    Permite comparar qué estrategias funcionan con dinero real, no sólo en
    backtest. Es el dato que, con suficiente historial, decide cuáles mantener.
    """
    cerradas = [t for t in trades if not t.is_open]
    if not cerradas:
        return []

    por_estrategia: dict[str, list[RealTrade]] = {}
    for t in cerradas:
        clave = t.strategy or "Sin señal (discrecional)"
        por_estrategia.setdefault(clave, []).append(t)

    filas = []
    for nombre, ops in sorted(por_estrategia.items()):
        resultados = [t.pnl() for t in ops]
        ganadoras = [r for r in resultados if r > 0]
        filas.append(
            {
                "Estrategia": nombre,
                "Operaciones": len(ops),
                "Resultado $": round(float(sum(resultados, start=Decimal(0))), 2),
                "Aciertos %": round(len(ganadoras) / len(ops) * 100, 1),
                "Días medios": round(
                    sum(t.days_held for t in ops) / len(ops), 1
                ),
            }
        )
    return filas
