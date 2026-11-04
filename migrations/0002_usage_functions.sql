-- 0002: SQL functions used by the usage viewer. Idempotent (CREATE OR REPLACE).
--
-- Approach
--   usage.event_slices(kind, ...) is the shared core. For events of the given kind
--   (window events must not be idle) overlapping [t_from, t_to) it:
--     1. clips each event to [t_from, t_to);
--     2. splits the clipped event pro rata over local-hour buckets, using
--        generate_series from time_bucket('1 hour', clipped_start, tz) to clipped_end;
--        slice seconds = overlap of the event with that hour;
--     3. subtracts idle time: per slice, a LEFT JOIN LATERAL sums the overlap with every
--        kind='afk' AND idle event of the same device, and the result is
--        greatest(0, slice_seconds - idle_overlap_seconds). (If idle events overlap each
--        other the sum can over-count; the clamp at 0 keeps the result sane.)
--   hourly / top_apps / top_domains / daily aggregate those slices. The hour-of-day
--   filter (inclusive, in tz) applies to the slice's bucket hour. A NULL or empty
--   devices array means all devices.

CREATE OR REPLACE FUNCTION usage.event_slices(
  p_kind text, devices text[], t_from timestamptz, t_to timestamptz, tz text,
  app_filter text DEFAULT NULL
) RETURNS TABLE(bucket timestamptz, device text, app text, domain text, seconds double precision)
LANGUAGE sql STABLE AS $$
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
    FROM usage.events a
    WHERE a.kind = 'afk' AND a.idle AND a.device = e.device
      AND a.ts_start < s.s_end AND a.ts_end > s.s_start
  ) idle ON true
  WHERE e.kind = p_kind
    AND (p_kind <> 'window' OR NOT e.idle)
    AND e.ts_start < t_to AND e.ts_end > t_from
    AND s.s_end > s.s_start
    AND (devices IS NULL OR cardinality(devices) = 0 OR e.device = ANY(devices))
    AND (app_filter IS NULL OR lower(e.app) = lower(app_filter))
$$;

CREATE OR REPLACE FUNCTION usage.hourly(
  devices text[], t_from timestamptz, t_to timestamptz, tz text, app_filter text DEFAULT NULL
) RETURNS TABLE(bucket timestamptz, device text, app text, seconds double precision)
LANGUAGE sql STABLE AS $$
  SELECT s.bucket, s.device, s.app, sum(s.seconds)::double precision
  FROM usage.event_slices('window', devices, t_from, t_to, tz, app_filter) s
  GROUP BY s.bucket, s.device, s.app
  ORDER BY s.bucket, s.device, s.app
$$;

CREATE OR REPLACE FUNCTION usage.top_apps(
  devices text[], t_from timestamptz, t_to timestamptz, hour_from int, hour_to int,
  tz text, lim int
) RETURNS TABLE(app text, seconds double precision)
LANGUAGE sql STABLE AS $$
  SELECT s.app, sum(s.seconds)::double precision AS total
  FROM usage.event_slices('window', devices, t_from, t_to, tz) s
  WHERE extract(hour FROM s.bucket AT TIME ZONE tz) BETWEEN hour_from AND hour_to
  GROUP BY s.app
  ORDER BY total DESC, s.app
  LIMIT lim
$$;

CREATE OR REPLACE FUNCTION usage.top_domains(
  devices text[], t_from timestamptz, t_to timestamptz, hour_from int, hour_to int,
  tz text, lim int
) RETURNS TABLE(domain text, seconds double precision)
LANGUAGE sql STABLE AS $$
  SELECT s.domain, sum(s.seconds)::double precision AS total
  FROM usage.event_slices('web', devices, t_from, t_to, tz) s
  WHERE extract(hour FROM s.bucket AT TIME ZONE tz) BETWEEN hour_from AND hour_to
  GROUP BY s.domain
  ORDER BY total DESC, s.domain
  LIMIT lim
$$;

CREATE OR REPLACE FUNCTION usage.daily(
  devices text[], t_from timestamptz, t_to timestamptz, tz text
) RETURNS TABLE(day date, device text, seconds double precision)
LANGUAGE sql STABLE AS $$
  SELECT (s.bucket AT TIME ZONE tz)::date AS d, s.device, sum(s.seconds)::double precision
  FROM usage.event_slices('window', devices, t_from, t_to, tz) s
  GROUP BY d, s.device
  ORDER BY d, s.device
$$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'usage_reader') THEN
    GRANT EXECUTE ON FUNCTION
      usage.event_slices(text, text[], timestamptz, timestamptz, text, text),
      usage.hourly(text[], timestamptz, timestamptz, text, text),
      usage.top_apps(text[], timestamptz, timestamptz, int, int, text, int),
      usage.top_domains(text[], timestamptz, timestamptz, int, int, text, int),
      usage.daily(text[], timestamptz, timestamptz, text)
    TO usage_reader;
  END IF;
END $$;

INSERT INTO usage.schema_migrations VALUES ('0002') ON CONFLICT DO NOTHING;
