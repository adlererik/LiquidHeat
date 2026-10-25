-- Additive only: prospectively recorded transport evidence and forecasts.
CREATE TABLE IF NOT EXISTS learning_control (
  protocol text PRIMARY KEY, activated_at timestamptz NOT NULL DEFAULT now(),
  last_review_at timestamptz, status jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS learning_feed_windows (
  ts timestamptz NOT NULL, exchange text NOT NULL, session_id text NOT NULL,
  first_seen timestamptz NOT NULL, last_seen timestamptz NOT NULL,
  messages integer NOT NULL, max_gap_s double precision NOT NULL,
  max_delay_s double precision NOT NULL, liquidation_ack boolean NOT NULL,
  PRIMARY KEY(ts,exchange,session_id)
);
CREATE INDEX IF NOT EXISTS learning_feed_lookup ON learning_feed_windows(exchange,ts);
CREATE TABLE IF NOT EXISTS learning_event_receipts (
  ts timestamptz NOT NULL, exchange text NOT NULL, event_id text NOT NULL,
  received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(ts,exchange,event_id)
);
CREATE INDEX IF NOT EXISTS learning_receipt_lookup ON learning_event_receipts(exchange,ts,received_at);
CREATE TABLE IF NOT EXISTS learning_models (
  id text PRIMARY KEY, protocol text NOT NULL, exchange text NOT NULL, side text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), trained_through timestamptz NOT NULL,
  decision_at timestamptz NOT NULL, decided_at timestamptz,
  state text NOT NULL CHECK(state IN ('candidate','champion','rejected','retired','rolled_back')),
  artifact jsonb NOT NULL, report jsonb NOT NULL DEFAULT '{}',
  CHECK(trained_through<=created_at AND decision_at>created_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS learning_one_candidate ON learning_models(protocol,exchange,side) WHERE state='candidate';
CREATE UNIQUE INDEX IF NOT EXISTS learning_one_champion ON learning_models(protocol,exchange,side) WHERE state='champion';
CREATE TABLE IF NOT EXISTS learning_forecasts (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  protocol text NOT NULL, exchange text NOT NULL, side text NOT NULL,
  issued_at timestamptz NOT NULL, target_start timestamptz NOT NULL, target_end timestamptz NOT NULL,
  source_ts timestamptz NOT NULL, features jsonb NOT NULL, provenance jsonb NOT NULL,
  baseline_typical_usd double precision NOT NULL CHECK(baseline_typical_usd>=0),
  baseline_persistence_usd double precision NOT NULL CHECK(baseline_persistence_usd>=0),
  champion_id text, champion_usd double precision NOT NULL CHECK(champion_usd>=0),
  candidate_id text, candidate_usd double precision CHECK(candidate_usd>=0),
  outcome_usd double precision, labelled_at timestamptz,
  eligible boolean NOT NULL DEFAULT false, reason text NOT NULL DEFAULT 'awaiting outcome and coverage audit',
  CHECK(issued_at<target_start AND source_ts<=issued_at AND target_end>target_start),
  UNIQUE(protocol,exchange,side,target_start)
);
CREATE INDEX IF NOT EXISTS learning_forecast_training ON learning_forecasts(protocol,exchange,side,target_end DESC);
CREATE INDEX IF NOT EXISTS learning_forecast_pending ON learning_forecasts(target_end) WHERE labelled_at IS NULL;
CREATE INDEX IF NOT EXISTS learning_forecast_candidates ON learning_forecasts(candidate_id,target_start) WHERE candidate_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS learning_decisions (
  id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, ts timestamptz NOT NULL DEFAULT now(),
  model_id text NOT NULL, action text NOT NULL, report jsonb NOT NULL
);
INSERT INTO schema_migrations(version) VALUES('003') ON CONFLICT DO NOTHING;
