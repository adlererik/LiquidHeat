-- Additive: public account observations are separate from inferred CEX exposure.
CREATE TABLE hl_accounts (
 address text PRIMARY KEY CHECK(address ~ '^0x[0-9a-f]{40}$'),
 discovered_at timestamptz NOT NULL DEFAULT now(), discovery text NOT NULL,
 priority double precision NOT NULL DEFAULT 0,
 attempted_at timestamptz, observed_at timestamptz,
 position jsonb, error_count integer NOT NULL DEFAULT 0
);
CREATE INDEX hl_accounts_scan ON hl_accounts(attempted_at NULLS FIRST);
CREATE TABLE hl_snapshots (
 ts timestamptz NOT NULL PRIMARY KEY, mark double precision NOT NULL CHECK(mark>0),
 grid_start double precision NOT NULL, grid_n integer NOT NULL CHECK(grid_n>0),
 matrix bytea NOT NULL, constituents bytea NOT NULL, audit jsonb NOT NULL,
 method_version text NOT NULL DEFAULT 'hyperliquid-reported-v1'
);
SELECT create_hypertable('hl_snapshots','ts',if_not_exists=>true,chunk_time_interval=>INTERVAL '1 day');
INSERT INTO exchange_metadata(exchange,symbol,coverage) VALUES('hyperliquid','BTC-USDC',
 '{"method":"sampled public clearinghouseState; not a complete position census","history":"prospective observations only"}') ON CONFLICT DO NOTHING;
INSERT INTO schema_migrations(version) VALUES('004') ON CONFLICT DO NOTHING;
