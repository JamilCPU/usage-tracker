# Migrations

SQL migrations for the `usage` schema. This repo owns them; the lab-data-store repo only provides the server, the `usage` schema and the `usage_owner` role.

## Rules

- Applied as the role `usage_owner`, in filename order (`0001_...`, `0002_...`, `0003_...`). The web app's `migrate` init container applies pending files automatically on every start (applied versions are tracked in `usage.schema_migrations`).
- Each migration records itself in `usage.schema_migrations` (`INSERT ... ON CONFLICT DO NOTHING`).
- All SQL is idempotent (`IF NOT EXISTS`, `if_not_exists => TRUE`), so re-applying is safe.

## Files

| File | Contents |
| --- | --- |
| `0001_usage_events.sql` | `usage.schema_migrations`, the `usage.events` hypertable (7-day chunks) and its indexes |
| `0002_usage_functions.sql` | Query functions used by the viewer (`event_slices`, `hourly`, `top_apps`, `top_domains`, `daily`) and their grants to `usage_reader` |
| `0003_idle_intervals.sql` | Partial index `events_afk_idle` and a cheaper `event_slices` that collects idle intervals once per query |

## Applying manually (one-time)

```bash
kubectl -n lab exec -i postgres-0 -- env PGPASSWORD="$(kubectl -n lab get secret lab-data-store -o jsonpath='{.data.USAGE_OWNER_PASSWORD}' | base64 -d)" psql -U usage_owner -d lab -v ON_ERROR_STOP=1 < migrations/0001_usage_events.sql
```

`scripts/apply-migrations.sh` wraps this for every file in order (`NAMESPACE` defaults to `lab`). In the cluster the init container does this, so the manual step is only for bootstrapping or debugging.

Verify with `SELECT hypertable_name FROM timescaledb_information.hypertables WHERE hypertable_schema = 'usage';` (should list `events`) and `SELECT version FROM usage.schema_migrations ORDER BY 1;` (should list `0001` to `0003`).

## Retention

Raw events are kept forever. No compression yet: upserts land in recent chunks. Revisit after a year.
