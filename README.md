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

### Linux desktop

Installs ActivityWatch and the sync shim as systemd user services. Needs `python3` (with `venv`), `curl` and an X11 session; run from a clone of this repo as the desktop user.

```bash
# 1. ActivityWatch (AppImage extracted under ~/.local/opt/activitywatch; units for server, afk and window watchers)
sync/install/linux/install-aw.sh            # AW_VERSION=v0.14.1 by default

# 2. The shim (venv in ~/.local/opt/usage-sync, symlink in ~/.local/bin, unit enabled but not started)
sync/install/linux/install-sync.sh

# 3. Config, pointing at the ingest API and the CA that signed its certificate
usage-sync init --device <device-name> --ingest-url https://<ingest-host> --ca-file <path-to-ca.pem>

# 4. Token: mint one on a machine with kubectl access (lab-data-store/scripts/mint-token.sh), store it mode 600
( umask 077; mint-token.sh <device-name> usage > ~/.config/usage-sync/token )

# 5. Check, then start
usage-sync status
usage-sync once                              # exit code 1 if any bucket failed to ship
systemctl --user start usage-sync
journalctl --user -u usage-sync -n 20
```

`sync/install/linux/config.example.toml` lists every option. Events that could not be shipped (ingest down, network out) stay in ActivityWatch; the shim re-reads from its saved cursor with an overlap, so they are shipped with their original timestamps once the ingest API is back.

_Remaining steps filled in by later tasks._

### Web viewer

`web/` is a FastAPI service (JSON API plus a Svelte single-page UI) that reads the `usage` schema as the read-only `usage_reader` role. It is served at `https://usage.lab` and signs users in through the lab's OAuth gateway (authorization code with PKCE, public client).

Deploy:

1. On the primary node, from this repo: `scripts/build-push.sh` builds the multi-arch image from `web/Dockerfile` (context = repo root, so `migrations/` is baked in) and prints `TAG=<sha>`.
2. Once: `scripts/register-oauth-client.sh` registers the OAuth client (dynamic client registration against the gateway, using the lab CA) and creates the Secret `usage-web` (`OAUTH_CLIENT_ID`, `SESSION_SECRET`). It never prints the values and does nothing if the Secret exists.
3. Bump the tag in lab-gitops `apps/usage-tracker/kustomization.yaml`; Flux applies it. A `migrate` init container runs `usage-web migrate` as `usage_owner` on every start (applied migrations are tracked, so it is idempotent).

Environment variables:

| Variable | Meaning |
| --- | --- |
| `USAGE_READER_DSN` | Postgres DSN of the read-only role (required) |
| `USAGE_OWNER_DSN` | DSN of the owner role, only for `usage-web migrate` |
| `USAGE_TZ` | Time zone for day and hour filters (default `America/Chicago`) |
| `SESSION_SECRET` | Signs session cookies, at least 16 characters (required) |
| `PUBLIC_URL` | Public URL, used for the OAuth redirect URI (default `https://usage.lab`) |
| `OAUTH_ISSUER`, `OAUTH_CLIENT_ID`, `OAUTH_SCOPE` | OAuth gateway issuer, client id and optional scope |
| `OAUTH_CA_FILE` | CA bundle that verifies the gateway certificate |
| `OAUTH_CONNECT_HOST` | Connect to the issuer at this host (keeping its Host and SNI); needed in-cluster, where the issuer name resolves to the wrong Service |
| `AUTH_MODE` | `oauth` (default) or `none` (local development only) |
| `MIGRATIONS_DIR`, `PORT` | Migrations directory (default `/app/migrations`) and listen port (default `8080`) |

Local development: build the frontend (`cd web/frontend && npm ci && npm run build`), then run `AUTH_MODE=none SESSION_SECRET=local-only-not-secret-0000 USAGE_READER_DSN=... uv run usage-web serve` from `web/`.

## License

MIT
