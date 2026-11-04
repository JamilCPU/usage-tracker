# Migrations

SQL migrations for the `usage` schema. This repo owns them; the lab-data-store repo only provides the server, the `usage` schema and the `usage_owner` role.

## Rules

- Applied as the role `usage_owner`, in filename order (`0001_...`, `0002_...`).
- Each migration records itself in `usage.schema_migrations` (`INSERT ... ON CONFLICT DO NOTHING`).
- All SQL is idempotent (`IF NOT EXISTS`, `if_not_exists => TRUE`), so re-applying is safe.

## Files

| File | Contents |
| --- | --- |
| `0001_usage_events.sql` | `usage.schema_migrations`, the `usage.events` hypertable (7-day chunks) and its indexes |

## Applying manually (one-time)

```bash
kubectl -n lab exec -i postgres-0 -- env PGPASSWORD="$(kubectl -n lab get secret lab-data-store -o jsonpath='{.data.USAGE_OWNER_PASSWORD}' | base64 -d)" psql -U usage_owner -d lab -v ON_ERROR_STOP=1 < migrations/0001_usage_events.sql
```

`scripts/apply-migrations.sh` wraps this for every file in order (`NAMESPACE` defaults to `lab`). The web app's init container applies migrations automatically later, replacing the manual step.

Verify with `\d usage.events` in psql: the table should be listed as a hypertable on `ts_start`.

## Retention

Raw events are kept forever. No compression yet: upserts land in recent chunks. Revisit after a year.
