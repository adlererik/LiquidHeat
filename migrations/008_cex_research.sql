-- Independent CEX experiments. No original table is altered or cleared.
CREATE TABLE IF NOT EXISTS cex_runs (
 exchange text PRIMARY KEY,start_ts bigint NOT NULL,initial_cutoff bigint NOT NULL,
 last_ts bigint NOT NULL,inputs bigint NOT NULL DEFAULT 0,profiles bigint NOT NULL DEFAULT 0,
 input_digest text NOT NULL DEFAULT '',status text NOT NULL DEFAULT 'replaying',
 detail text NOT NULL DEFAULT '',updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS cex_inputs (
 ts timestamptz NOT NULL,exchange text NOT NULL,payload bytea NOT NULL,digest text NOT NULL,
 PRIMARY KEY(ts,exchange)
);
CREATE TABLE IF NOT EXISTS cex_checkpoints (
 exchange text NOT NULL,scenario text NOT NULL,ts timestamptz NOT NULL,
 meta jsonb NOT NULL,matrix bytea NOT NULL,metrics jsonb NOT NULL,PRIMARY KEY(exchange,scenario)
);
CREATE TABLE IF NOT EXISTS cex_snapshots (
 ts timestamptz NOT NULL,exchange text NOT NULL,scenario text NOT NULL,
 grid_start double precision NOT NULL,grid_n integer NOT NULL,mark double precision NOT NULL,
 matrix bytea NOT NULL,audit jsonb NOT NULL,PRIMARY KEY(ts,exchange,scenario)
);
CREATE INDEX IF NOT EXISTS cex_snapshots_query ON cex_snapshots(scenario,exchange,ts DESC);
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='btc_liq_cex_worker') THEN
  CREATE ROLE btc_liq_cex_worker NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT;
 END IF;
END $$;
GRANT USAGE ON SCHEMA public TO btc_liq_cex_worker;
GRANT SELECT ON candles,open_interest,funding_ratios,volume_profiles,contract_specs,
 maintenance_margin_brackets,leverage_parameters,ingestion_checkpoints,model_checkpoints,
 heatmap_snapshots TO btc_liq_cex_worker;
GRANT SELECT,INSERT,UPDATE,DELETE ON cex_runs,cex_inputs,cex_checkpoints,cex_snapshots TO btc_liq_cex_worker;
INSERT INTO schema_migrations(version) VALUES('008') ON CONFLICT DO NOTHING;
