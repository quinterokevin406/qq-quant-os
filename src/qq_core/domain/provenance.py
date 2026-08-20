"""Trazabilidad de datos.

Regla del Módulo 01: ninguna barra entra al sistema sin procedencia. Si no se
puede responder "¿de dónde salió este número, cuándo llegó y qué versión de
código lo transformó?", el dato no se almacena.

Esto no es burocracia. Es la única forma de que un backtest ejecutado hoy sea
reproducible dentro de tres años, cuando el proveedor haya revisado su
histórico silenciosamente (cosa que hacen todos, incluido MT5 al recargar el
histórico del bróker).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from qq_core.domain.enums import DataSource

TRANSFORM_VERSION = "1.0.0"
"""Versión del pipeline de transformación del Módulo 01.

Se incrementa cuando cambia CUALQUIER lógica que altere los valores
almacenados (parseo, redondeo, conversión de zona horaria). Un cambio aquí
obliga a una reingesta completa, y permite detectar filas escritas por una
versión anterior sin borrar nada.
"""


class Provenance(BaseModel):
    """Metadatos de origen de un lote de barras.

    Attributes:
        source: Proveedor del que se obtuvo el dato.
        provider_symbol: Símbolo tal como lo nombra el proveedor, antes del
            mapeo canónico. Ej. `EURUSD.gbi`, `es.f`, `DGS10`.
        ingested_at: Instante UTC en que el dato entró al sistema.
        transform_version: Versión del código que produjo los valores.
        raw_payload_hash: SHA-256 de la respuesta cruda del proveedor. Permite
            detectar que el proveedor cambió el histórico sin avisar: mismo
            rango solicitado, hash distinto.
        request_params: Parámetros exactos de la petición. Necesarios para
            reproducir la llamada.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    source: DataSource
    provider_symbol: str = Field(min_length=1, max_length=64)
    ingested_at: datetime
    transform_version: str = TRANSFORM_VERSION
    raw_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ingested_at")
    @classmethod
    def _must_be_utc_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            raise ValueError("ingested_at debe ser timezone-aware en UTC")
        return v


def hash_payload(payload: bytes | str) -> str:
    """Calcula el SHA-256 hexadecimal de una respuesta cruda.

    Args:
        payload: Respuesta del proveedor, en bytes o texto.

    Returns:
        Digest hexadecimal de 64 caracteres.
    """
    data = payload.encode("utf-8") if isinstance(payload, str) else payload
    return hashlib.sha256(data).hexdigest()


def content_hash(fields: dict[str, Any]) -> str:
    """Hash determinista del contenido de una barra.

    Se usa para idempotencia: si el hash de contenido de una barra entrante
    coincide con el almacenado, el UPSERT es un no-op y `updated_at` no cambia.
    Si difiere, el proveedor revisó el dato y eso debe quedar registrado.

    El serializado usa `sort_keys=True` y `default=str` para que el mismo
    contenido produzca siempre el mismo hash independientemente del orden de
    las claves o del tipo exacto (`Decimal` vs `str`).

    Args:
        fields: Campos de contenido de la barra (sin metadatos mutables).

    Returns:
        Digest hexadecimal de 64 caracteres.
    """
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
