-- =====================================================================
-- QQ Quant OS — Módulo 01 Data Engine — Esquema TimescaleDB
-- transform_version: 1.0.0
-- =====================================================================
-- Principios:
--  1. La clave primaria ES la clave natural. Sin ids sintéticos: hacen
--     posible insertar el mismo dato dos veces con ids distintos.
--  2. `source` forma parte de la PK. Barras de GBI y de Stooq para el mismo
--     símbolo coexisten. Resolverlas es trabajo del Módulo 02.
--  3. NUMERIC, nunca DOUBLE PRECISION, para precios.
--  4. Nada se borra. Las revisiones del proveedor se registran, no se pisan
--     en silencio.
-- =====================================================================

CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ---------------------------------------------------------------------
-- Catálogo de instrumentos
-- ---------------------------------------------------------------------
-- Los atributos específicos de clase viven en `spec` (JSONB) porque un
-- esquema relacional con una tabla por clase de activo multiplica los JOINs
-- sin aportar integridad real: la validación fuerte ya ocurre en Pydantic,
-- en la frontera de la aplicación. La restricción CHECK garantiza que las
-- claves obligatorias de cada clase estén presentes.
CREATE TABLE IF NOT EXISTS instrument (
    symbol            TEXT        PRIMARY KEY,
    asset_class       TEXT        NOT NULL,
    name              TEXT        NOT NULL,
    currency          CHAR(3),
    tick_size         NUMERIC(20, 10) NOT NULL CHECK (tick_size > 0),
    quote_type        TEXT        NOT NULL DEFAULT 'price',
    active            BOOLEAN     NOT NULL DEFAULT TRUE,
    spec              JSONB       NOT NULL DEFAULT '{}'::JSONB,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT instrument_symbol_canonical CHECK (symbol = UPPER(symbol)),
    CONSTRAINT instrument_future_spec CHECK (
        asset_class <> 'future'
        OR (spec ? 'expiry' AND spec ? 'contract_multiplier' AND spec ? 'exchange')
    ),
    CONSTRAINT instrument_cfd_spec CHECK (
        asset_class <> 'cfd' OR (spec ? 'issuer' AND spec ? 'underlying_ref')
    )
);

-- ---------------------------------------------------------------------
-- Mapeo símbolo proveedor -> símbolo canónico
-- ---------------------------------------------------------------------
-- Tabla separada, no columna en `instrument`: un mismo instrumento se ingesta
-- desde varios proveedores con nombres distintos, y esa relación es N:1.
CREATE TABLE IF NOT EXISTS symbol_map (
    source            TEXT        NOT NULL,
    provider_symbol   TEXT        NOT NULL,
    symbol            TEXT        NOT NULL REFERENCES instrument(symbol),
    valid_from        DATE        NOT NULL DEFAULT '1900-01-01',
    valid_to          DATE        NOT NULL DEFAULT '9999-12-31',
    PRIMARY KEY (source, provider_symbol, valid_from)
);
-- `valid_from`/`valid_to` existen para los cambios de ticker: cuando FB pasó
-- a META, el histórico anterior sigue siendo alcanzable sin reescribir nada.

CREATE INDEX IF NOT EXISTS symbol_map_symbol_idx ON symbol_map (symbol);

-- ---------------------------------------------------------------------
-- Procedencia
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS provenance (
    provenance_id     BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source            TEXT        NOT NULL,
    provider_symbol   TEXT        NOT NULL,
    ingested_at       TIMESTAMPTZ NOT NULL,
    transform_version TEXT        NOT NULL,
    raw_payload_hash  CHAR(64)    NOT NULL,
    request_params    JSONB       NOT NULL DEFAULT '{}'::JSONB
);

-- ---------------------------------------------------------------------
-- Barras (hypertable)
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bar (
    symbol            TEXT        NOT NULL REFERENCES instrument(symbol),
    timeframe         TEXT        NOT NULL,
    ts                TIMESTAMPTZ NOT NULL,
    source            TEXT        NOT NULL,

    open              NUMERIC(20, 10) NOT NULL,
    high              NUMERIC(20, 10) NOT NULL,
    low               NUMERIC(20, 10) NOT NULL,
    close             NUMERIC(20, 10) NOT NULL,
    volume            NUMERIC(24, 6),
    open_interest     NUMERIC(24, 6),

    content_hash      CHAR(64)    NOT NULL,
    provenance_id     BIGINT      NOT NULL REFERENCES provenance(provenance_id),
    first_seen_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- La invariante OHLC se valida en Pydantic, pero también aquí: la base de
    -- datos es la última línea de defensa contra un script ad-hoc que escriba
    -- saltándose la aplicación.
    CONSTRAINT bar_ohlc_coherent CHECK (
        high >= low AND high >= GREATEST(open, close) AND low <= LEAST(open, close)
    ),
    CONSTRAINT bar_volume_non_negative CHECK (volume IS NULL OR volume >= 0),
    PRIMARY KEY (symbol, timeframe, source, ts)
);

SELECT create_hypertable(
    'bar', 'ts',
    partitioning_column => 'symbol',
    number_partitions   => 16,
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists       => TRUE
);

-- ---------------------------------------------------------------------
-- Registro de revisiones del proveedor
-- ---------------------------------------------------------------------
-- Cuando un proveedor cambia un dato histórico, NO se pierde el valor
-- anterior. Sin esta tabla es imposible explicar por qué un backtest de hace
-- seis meses ya no reproduce.
CREATE TABLE IF NOT EXISTS bar_revision (
    symbol            TEXT        NOT NULL,
    timeframe         TEXT        NOT NULL,
    ts                TIMESTAMPTZ NOT NULL,
    source            TEXT        NOT NULL,
    detected_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    old_content_hash  CHAR(64)    NOT NULL,
    new_content_hash  CHAR(64)    NOT NULL,
    old_values        JSONB       NOT NULL,
    new_values        JSONB       NOT NULL,
    PRIMARY KEY (symbol, timeframe, source, ts, detected_at)
);

-- ---------------------------------------------------------------------
-- UPSERT idempotente
-- ---------------------------------------------------------------------
-- `WHERE bar.content_hash <> EXCLUDED.content_hash` es la línea clave: si el
-- contenido es idéntico, la fila NO se escribe y `updated_at` no cambia.
-- Reingestar el mismo rango deja la tabla bit a bit igual.
CREATE OR REPLACE FUNCTION upsert_bar(payload JSONB, p_provenance_id BIGINT)
RETURNS TEXT AS $$
DECLARE
    v_existing_hash CHAR(64);
    v_new_hash      CHAR(64) := payload->>'content_hash';
BEGIN
    SELECT content_hash INTO v_existing_hash
    FROM bar
    WHERE symbol    = payload->>'symbol'
      AND timeframe = payload->>'timeframe'
      AND source    = payload->>'source'
      AND ts        = (payload->>'ts')::TIMESTAMPTZ;

    IF v_existing_hash IS NOT NULL AND v_existing_hash = v_new_hash THEN
        RETURN 'unchanged';
    END IF;

    IF v_existing_hash IS NOT NULL THEN
        INSERT INTO bar_revision (
            symbol, timeframe, ts, source,
            old_content_hash, new_content_hash, old_values, new_values
        )
        SELECT b.symbol, b.timeframe, b.ts, b.source,
               b.content_hash, v_new_hash, to_jsonb(b), payload
        FROM bar b
        WHERE b.symbol    = payload->>'symbol'
          AND b.timeframe = payload->>'timeframe'
          AND b.source    = payload->>'source'
          AND b.ts        = (payload->>'ts')::TIMESTAMPTZ;
    END IF;

    INSERT INTO bar (
        symbol, timeframe, ts, source, open, high, low, close,
        volume, open_interest, content_hash, provenance_id
    ) VALUES (
        payload->>'symbol',
        payload->>'timeframe',
        (payload->>'ts')::TIMESTAMPTZ,
        payload->>'source',
        (payload->>'open')::NUMERIC,
        (payload->>'high')::NUMERIC,
        (payload->>'low')::NUMERIC,
        (payload->>'close')::NUMERIC,
        (payload->>'volume')::NUMERIC,
        (payload->>'open_interest')::NUMERIC,
        v_new_hash,
        p_provenance_id
    )
    ON CONFLICT (symbol, timeframe, source, ts) DO UPDATE
    SET open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
        close = EXCLUDED.close, volume = EXCLUDED.volume,
        open_interest = EXCLUDED.open_interest,
        content_hash = EXCLUDED.content_hash,
        provenance_id = EXCLUDED.provenance_id,
        updated_at = NOW()
    WHERE bar.content_hash <> EXCLUDED.content_hash;

    RETURN CASE WHEN v_existing_hash IS NULL THEN 'inserted' ELSE 'revised' END;
END;
$$ LANGUAGE plpgsql;

-- ---------------------------------------------------------------------
-- Watermarks: hace reanudable la ingesta
-- ---------------------------------------------------------------------
CREATE MATERIALIZED VIEW IF NOT EXISTS ingestion_watermark AS
SELECT symbol, timeframe, source, MAX(ts) AS last_ts, COUNT(*) AS bar_count
FROM bar
GROUP BY symbol, timeframe, source;

CREATE UNIQUE INDEX IF NOT EXISTS ingestion_watermark_pk
    ON ingestion_watermark (symbol, timeframe, source);

-- ---------------------------------------------------------------------
-- Compresión
-- ---------------------------------------------------------------------
-- 90 días es deliberadamente conservador: los chunks comprimidos son
-- costosos de modificar, y las revisiones del proveedor suelen llegar dentro
-- de las primeras semanas.
ALTER TABLE bar SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'symbol, timeframe, source',
    timescaledb.compress_orderby   = 'ts DESC'
);

SELECT add_compression_policy('bar', INTERVAL '90 days', if_not_exists => TRUE);

-- NOTA: no se define política de retención. En una plataforma de
-- investigación cuantitativa, borrar histórico es destruir el activo
-- principal de la empresa.
