# usage-tracker

Cross-device app and website usage tracking: see which apps and sites are used, and when, across a Windows PC, a Linux desktop and an Android phone. [ActivityWatch](https://activitywatch.net/) watchers collect events on each device; a small sync shim ships settled events, with their original timestamps, to the [lab-data-store](https://github.com/JamilCPU/lab-data-store) ingest API. Events land in a `usage` schema on TimescaleDB, and a web viewer lets you explore them with device, date and hour filters.

## Architecture

```
 GPU PC ───────────┐                                 ┌─ usage web viewer (any node)
 Linux desktop ────┤ usage client ──HTTPS──► ingest-api ─┤
 (primary node)    │ (buffer, ship)   (any node)         ├─ future: more read-only MCP servers
 Android phone ────┘                         │           │
                                             ▼           │
                                 Postgres + TimescaleDB ◄┘
                          (PINNED to the primary node, hostPath data)
                   schemas: usage | bank | sms | email | homestudio | ...
                   nightly pg_dump to a second disk; weekly off-node pull
```

- **Watchers**: ActivityWatch on each device records the active app and browser tab.
- **Sync shim**: periodically reads settled events from the local ActivityWatch instance and posts them in batches to the ingest API with a per-device token; original timestamps are preserved and re-sent windows are de-duplicated.
- **Schema**: the `usage` schema and its migrations live in this repo; the store repo only provides the server, roles and conventions.
- **Web viewer**: reads the `usage` schema through a read-only role and filters by device, date and hour.

## Repo layout

| Path | Contents |
| --- | --- |
| `migrations/` | SQL migrations for the `usage` schema |
| `sync/` | ActivityWatch sync shim (Python) |
| `web/` | Web viewer (API and UI) |
| `scripts/` | Install and operational helpers |
| `.github/workflows/` | CI (lint and tests for each Python package once present) |

## Quick start

### Schema

The `usage` schema (events hypertable) is created by the SQL in `migrations/`. See [`migrations/README.md`](migrations/README.md) for how it is applied.

_Remaining steps filled in by later tasks._

## License

MIT
