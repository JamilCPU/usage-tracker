-- 0003: cheaper idle subtraction. Idempotent.
--
--   * Partial index over the idle AFK intervals so they can be found without scanning
--     every event of the device.
--   * usage.event_slices now collects the idle intervals ONCE (a materialized CTE bounded
--     by the query window and the device filter) and each slice joins against that small
--     set, instead of a lateral scan of usage.events per slice bounded only by
--     ts_start < s_end. Results are identical to 0002: the per-slice idle seconds are still
--     the sum of the overlaps with every idle interval of the same device, clamped at 0.

CREATE INDEX IF NOT EXISTS events_afk_idle ON usage.events (device, ts_end)
  WHERE kind = 'afk' AND idle;

CREATE OR REPLACE FUNCTION usage.event_slices(
  p_kind text, devices text[], t_from timestamptz, t_to timestamptz, tz text,
  app_filter text DEFAULT NULL
) RETURNS TABLE(bucket timestamptz, device text, app text, domain text, seconds double precision)
LANGUAGE sql STABLE AS $$
  WITH idle_iv AS MATERIALIZED (
    SELECT a.device, a.ts_start, a.ts_end
    FROM usage.events a
    WHERE a.kind = 'afk' AND a.idle
      AND a.ts_end > t_from AND a.ts_start < t_to
      AND (devices IS NULL OR cardinality(devices) = 0 OR a.device = ANY(devices))
  )
  SELECT g.bucket, e.device, e.app, e.domain,
         greatest(0, extract(epoch FROM (s.s_end - s.s_start)) - coalesce(idle.secs, 0))
  FROM usage.events e
  CROSS JOIN LATERAL (
    SELECT greatest(e.ts_start, t_from) AS c_start, least(e.ts_end, t_to) AS c_end
  ) c
  CROSS JOIN LATERAL generate_series(
    time_bucket(interval '1 hour', c.c_start, tz), c.c_end, interval '1 hour'
  ) AS g(bucket)
  CROSS JOIN LATERAL (
    SELECT greatest(c.c_start, g.bucket) AS s_start,
           least(c.c_end, g.bucket + interval '1 hour') AS s_end
  ) s
  LEFT JOIN LATERAL (
    SELECT sum(extract(epoch FROM (least(a.ts_end, s.s_end) - greatest(a.ts_start, s.s_start))))
             AS secs
    FROM idle_iv a
    WHERE a.device = e.device AND a.ts_start < s.s_end AND a.ts_end > s.s_start
  ) idle ON true
  WHERE e.kind = p_kind
    AND (p_kind <> 'window' OR NOT e.idle)
    AND e.ts_start < t_to AND e.ts_end > t_from
    AND s.s_end > s.s_start
    AND (devices IS NULL OR cardinality(devices) = 0 OR e.device = ANY(devices))
    AND (app_filter IS NULL OR lower(e.app) = lower(app_filter))
$$;

INSERT INTO usage.schema_migrations VALUES ('0003') ON CONFLICT DO NOTHING;
