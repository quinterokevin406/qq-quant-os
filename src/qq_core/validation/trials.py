"""Registro de ensayos: cuántas configuraciones se han probado realmente.

POR QUÉ ESTE FICHERO ES EL MÁS IMPORTANTE DEL MÓDULO
------------------------------------------------------
Todas las correcciones estadísticas de este módulo necesitan un número: cuántos
candidatos se evaluaron antes de quedarse con el mejor. Ese número no es una
formalidad. Es el que determina cuánto hay que descontar.

Y aquí está el problema que este fichero intenta resolver:

    El número correcto NO es 207.

207 son las combinaciones que el sistema evalúa hoy. El número que exigen las
correcciones incluye TODO lo que se probó durante el desarrollo: cada ventana
de media móvil que se cambió, cada umbral que se movió, cada nivel de stop que
se ajustó mirando el resultado. Cada uno de esos ajustes es un ensayo, aunque
nadie lo llamara así en su momento.

Ese recuento no existía. Nadie lo llevaba. A partir de este registro sí existe.

CONSECUENCIA QUE NO SE PUEDE ARREGLAR RETROACTIVAMENTE
--------------------------------------------------------
Los ensayos anteriores a la creación de este registro están perdidos. No se
pueden reconstruir: no quedó traza. Por tanto el recuento que este módulo
puede ofrecer hoy es UNA COTA INFERIOR del número real de ensayos, y todas las
correcciones que lo usan son, en consecuencia, OPTIMISTAS.

Esto no es un defecto de implementación que se pueda pulir. Es una limitación
del historial del proyecto y debe declararse en cada informe mientras siga
siendo cierta. La honestidad sobre esta limitación vale más que la cifra.

QUÉ CUENTA COMO UN ENSAYO
--------------------------
Cualquier evaluación cuyo resultado pudo influir en qué se conserva. Ejecutar
la misma configuración dos veces sobre los mismos datos no son dos ensayos: es
uno repetido, y el registro lo deduplica por huella. Cambiar un parámetro y
volver a mirar sí son dos.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ESQUEMA = """
CREATE TABLE IF NOT EXISTS trials (
    fingerprint   TEXT PRIMARY KEY,
    strategy      TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    symbol        TEXT NOT NULL,
    timeframe     TEXT NOT NULL,
    params_json   TEXT NOT NULL,
    sharpe        REAL,
    n_observations INTEGER,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    times_run     INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_trials_strategy ON trials(strategy);
CREATE INDEX IF NOT EXISTS idx_trials_symbol ON trials(symbol);
"""


@dataclass(frozen=True)
class Trial:
    """Una configuración evaluada.

    Attributes:
        strategy: Identificador de la estrategia.
        strategy_version: Versión, porque cambiar la lógica crea un candidato
            distinto aunque el nombre sea el mismo.
        symbol: Instrumento sobre el que se evaluó.
        timeframe: Resolución de los datos.
        params: Parámetros concretos. Dos juegos de parámetros distintos son
            dos ensayos, aunque la estrategia sea la misma.
        sharpe: Sharpe por observación obtenido, si se conoce.
        n_observations: Longitud de la muestra evaluada.
    """

    strategy: str
    strategy_version: str
    symbol: str
    timeframe: str = "1d"
    params: dict[str, Any] = field(default_factory=dict)
    sharpe: float | None = None
    n_observations: int | None = None

    @property
    def fingerprint(self) -> str:
        """Huella única de la configuración.

        Deliberadamente NO incluye el resultado: dos ejecuciones de la misma
        configuración son el mismo ensayo aunque den números ligeramente
        distintos por haberse añadido datos nuevos.
        """
        material = json.dumps(
            {
                "e": self.strategy,
                "v": self.strategy_version,
                "s": self.symbol,
                "t": self.timeframe,
                "p": self.params,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


class TrialRegistry:
    """Almacén persistente de ensayos.

    Usa SQLite en el mismo estilo que el resto del sistema. Se serializa el
    acceso con un cerrojo porque Streamlit crea un hilo por interacción, y ya
    hubo un fallo por esta causa en el módulo de persistencia.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.executescript(ESQUEMA)
        self._conn.commit()

    def record(self, trial: Trial) -> bool:
        """Anota un ensayo.

        Args:
            trial: Configuración evaluada.

        Returns:
            `True` si es un ensayo nuevo, `False` si ya estaba registrado y
            sólo se ha actualizado el contador de ejecuciones.
        """
        ahora = datetime.now(timezone.utc).isoformat()
        huella = trial.fingerprint

        with self._lock:
            cur = self._conn.execute(
                "SELECT times_run FROM trials WHERE fingerprint = ?", (huella,)
            )
            fila = cur.fetchone()

            if fila is None:
                self._conn.execute(
                    "INSERT INTO trials (fingerprint, strategy, strategy_version,"
                    " symbol, timeframe, params_json, sharpe, n_observations,"
                    " first_seen, last_seen, times_run)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,1)",
                    (
                        huella,
                        trial.strategy,
                        trial.strategy_version,
                        trial.symbol,
                        trial.timeframe,
                        json.dumps(trial.params, sort_keys=True, default=str),
                        trial.sharpe,
                        trial.n_observations,
                        ahora,
                        ahora,
                    ),
                )
                self._conn.commit()
                return True

            self._conn.execute(
                "UPDATE trials SET last_seen = ?, times_run = times_run + 1,"
                " sharpe = COALESCE(?, sharpe),"
                " n_observations = COALESCE(?, n_observations)"
                " WHERE fingerprint = ?",
                (ahora, trial.sharpe, trial.n_observations, huella),
            )
            self._conn.commit()
            return False

    def record_many(self, trials: list[Trial]) -> int:
        """Anota varios ensayos. Devuelve cuántos eran nuevos."""
        return sum(1 for t in trials if self.record(t))

    def count(self) -> int:
        """Número de ensayos distintos registrados.

        ATENCIÓN: es una COTA INFERIOR del número real. Ver la explicación al
        principio de este fichero.
        """
        with self._lock:
            cur = self._conn.execute("SELECT COUNT(*) FROM trials")
            return int(cur.fetchone()[0])

    def sharpe_variance(self) -> float:
        """Varianza de los Sharpe registrados.

        Es el segundo ingrediente que necesita el Deflated Sharpe Ratio: mide
        cuán dispersa fue la búsqueda. Una búsqueda muy dispersa produce por
        azar un máximo más alto, y por tanto exige un umbral más exigente.
        """
        with self._lock:
            cur = self._conn.execute(
                "SELECT sharpe FROM trials WHERE sharpe IS NOT NULL"
            )
            valores = [float(r[0]) for r in cur.fetchall()]

        if len(valores) < 2:
            return 0.0

        media = sum(valores) / len(valores)
        return sum((v - media) ** 2 for v in valores) / (len(valores) - 1)

    def summary(self) -> dict[str, Any]:
        """Resumen para mostrar en el panel."""
        with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*), COUNT(DISTINCT strategy), COUNT(DISTINCT symbol),"
                " SUM(times_run), MIN(first_seen) FROM trials"
            )
            total, n_est, n_sym, ejecuciones, desde = cur.fetchone()

        return {
            "ensayos_registrados": int(total or 0),
            "estrategias": int(n_est or 0),
            "instrumentos": int(n_sym or 0),
            "ejecuciones_totales": int(ejecuciones or 0),
            "registrando_desde": desde,
            "es_cota_inferior": True,
            "advertencia": (
                "Este recuento sólo incluye los ensayos posteriores a la "
                "creación del registro. Las configuraciones probadas durante "
                "el desarrollo previo no están contadas, por lo que el número "
                "real de ensayos es mayor y las correcciones estadísticas que "
                "usan esta cifra son optimistas."
            ),
        }

    def close(self) -> None:
        with self._lock:
            self._conn.close()
