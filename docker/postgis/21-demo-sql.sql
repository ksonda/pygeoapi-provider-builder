-- Demo data for the sql-features template (PLAN.md §13, Phase 4: "a multi-table join is
-- published and passes the harness"): a lookup table to join to pygeoapi's own
-- waterways fixture, and the least-privilege, read-only role ADR-0007 asks stewards
-- to connect with. Idempotent: tests/test_sql_postgis.py runs it on an existing stack.
-- The pb_reader password is a test value for this dev stack only.

CREATE TABLE IF NOT EXISTS osm.waterway_classes (
    waterway varchar(80) PRIMARY KEY,
    label text NOT NULL,
    description text,
    natural_channel boolean NOT NULL,
    reviewed_at timestamptz NOT NULL
);

INSERT INTO osm.waterway_classes VALUES
    ('river', 'River', 'A large natural watercourse', true, '2026-01-15T00:00:00Z'),
    ('stream', 'Stream', 'A small natural watercourse', true, '2026-02-01T00:00:00Z'),
    ('canal', 'Canal', 'An artificial open waterway', false, '2026-03-10T00:00:00Z'),
    ('drain', 'Drain', 'An artificial channel that carries water away', false, '2026-03-10T00:00:00Z'),
    ('ditch', 'Ditch', 'A small artificial channel', false, '2026-04-20T00:00:00Z'),
    ('dam', 'Dam', 'A barrier that holds water back', false, '2026-05-05T00:00:00Z'),
    ('spillway', 'Spillway', 'A channel that lets water out of a dam', false, '2026-05-05T00:00:00Z')
ON CONFLICT (waterway) DO NOTHING;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'pb_reader') THEN
        CREATE ROLE pb_reader LOGIN PASSWORD 'reader-password';
    END IF;
END
$$;
ALTER ROLE pb_reader SET default_transaction_read_only = on;
GRANT USAGE ON SCHEMA osm TO pb_reader;
GRANT SELECT ON osm.hotosm_bdi_waterways, osm.waterway_classes TO pb_reader;
