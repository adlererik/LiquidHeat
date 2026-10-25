CREATE TABLE IF NOT EXISTS model_evaluations(
  window_end timestamptz NOT NULL,
  exchange text NOT NULL,
  model_version text NOT NULL,
  evaluation_set text NOT NULL,
  observed_usd double precision NOT NULL,
  predicted_usd double precision NOT NULL,
  absolute_error_usd double precision NOT NULL,
  eligible_for_fitting boolean NOT NULL DEFAULT false,
  coverage_note text NOT NULL,
  PRIMARY KEY(window_end,exchange,model_version)
);
INSERT INTO schema_migrations(version) VALUES('002') ON CONFLICT DO NOTHING;
