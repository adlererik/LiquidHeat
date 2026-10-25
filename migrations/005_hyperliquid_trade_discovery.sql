-- Durable signals refresh a previously checked account after a large public fill.
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS signaled_at timestamptz;
ALTER TABLE hl_accounts ADD COLUMN IF NOT EXISTS largest_trade_usd double precision NOT NULL DEFAULT 0;
INSERT INTO schema_migrations(version) VALUES('005') ON CONFLICT DO NOTHING;
