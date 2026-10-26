-- Keep the new relationship lane cheap even with a full discovery queue.
CREATE INDEX IF NOT EXISTS hl_queue_unseen_linked ON hl_accounts(priority DESC,address)
 WHERE observed_at IS NULL AND discovery IN ('public_subaccount','public_vault','public_vault_child');
INSERT INTO schema_migrations(version) VALUES('007') ON CONFLICT DO NOTHING;
