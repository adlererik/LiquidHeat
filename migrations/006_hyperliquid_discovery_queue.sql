-- Additive queue upgrade. Frozen matrices and account observations are untouched.
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS next_check_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS tracking boolean NOT NULL DEFAULT false;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS last_seen_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS activity_window bigint NOT NULL DEFAULT 0;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS activity_usd double precision NOT NULL DEFAULT 0;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS last_trade_at timestamptz;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS subaccounts_next_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS vaults_next_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS vault_details_next_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS first_btc_at timestamptz;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS first_btc_source text;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS first_btc_size double precision;
UPDATE hl_accounts SET first_btc_at=observed_at,first_btc_source='pre-expansion',
 first_btc_size=abs((position->>'size_btc')::double precision)
 WHERE position IS NOT NULL AND first_btc_at IS NULL;
CREATE INDEX IF NOT EXISTS hl_queue_due ON hl_accounts(next_check_at,address);
CREATE INDEX IF NOT EXISTS hl_queue_refresh ON hl_accounts(next_check_at,address) WHERE tracking;
CREATE INDEX IF NOT EXISTS hl_queue_unseen ON hl_accounts(priority DESC,address) WHERE observed_at IS NULL;
CREATE INDEX IF NOT EXISTS hl_queue_signals ON hl_accounts(signaled_at DESC) WHERE signaled_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS hl_queue_sizes ON hl_accounts((abs((position->>'size_btc')::double precision)) DESC,address) WHERE position IS NOT NULL;
CREATE INDEX IF NOT EXISTS hl_queue_subaccounts ON hl_accounts(subaccounts_next_at,priority DESC);
CREATE INDEX IF NOT EXISTS hl_queue_vaults ON hl_accounts(vaults_next_at,priority DESC);
CREATE INDEX IF NOT EXISTS hl_queue_vault_details ON hl_accounts(vault_details_next_at) WHERE discovery IN ('public_vault','public_vault_child');
CREATE TABLE IF NOT EXISTS hl_discovery_trades (
 event_ms bigint NOT NULL,tid text NOT NULL,notional double precision NOT NULL CHECK(notional>0),
 buyer text NOT NULL,seller text NOT NULL,PRIMARY KEY(event_ms,tid)
);
CREATE TABLE IF NOT EXISTS hl_discovery_metrics (
 source text PRIMARY KEY,admitted bigint NOT NULL DEFAULT 0,
 capacity_dropped bigint NOT NULL DEFAULT 0,evicted bigint NOT NULL DEFAULT 0
);
INSERT INTO schema_migrations(version) VALUES('006') ON CONFLICT DO NOTHING;
