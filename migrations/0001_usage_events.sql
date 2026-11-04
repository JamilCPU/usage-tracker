CREATE TABLE IF NOT EXISTS usage.schema_migrations (version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE IF NOT EXISTS usage.events (
  id bigint GENERATED ALWAYS AS IDENTITY,
  device text NOT NULL, ts_start timestamptz NOT NULL, ts_end timestamptz NOT NULL,
  kind text NOT NULL CHECK (kind IN ('window','web','afk')),
  app text, title text, url text, domain text, idle boolean NOT NULL DEFAULT false,
  source_bucket text NOT NULL, source_event_id bigint NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  CHECK (ts_end >= ts_start),
  PRIMARY KEY (ts_start, id),
  UNIQUE (device, source_bucket, source_event_id, ts_start)
);
SELECT create_hypertable('usage.events', 'ts_start', chunk_time_interval => INTERVAL '7 days', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS events_device_ts ON usage.events (device, ts_start DESC);
CREATE INDEX IF NOT EXISTS events_app ON usage.events (app, ts_start DESC) WHERE kind = 'window';
CREATE INDEX IF NOT EXISTS events_domain ON usage.events (domain, ts_start DESC) WHERE kind = 'web';
INSERT INTO usage.schema_migrations VALUES ('0001') ON CONFLICT DO NOTHING;
