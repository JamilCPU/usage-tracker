# Windows install

Per-user, no admin rights. Run from a clone of this repo in PowerShell 5.1 or later:

```powershell
# preview first: prints every step, changes nothing
.\install.ps1 -DryRun

.\install.ps1 -Device <device-name> -IngestUrl https://<ingest-host> -CaFile <path-to-ca.pem> -NodeSsh <user>@<node>
```

What it does: downloads the ActivityWatch Windows zip to `%LOCALAPPDATA%\Programs\activitywatch`, registers logon task `ActivityWatch` (`aw-qt.exe`, tray only), builds a venv in `%LOCALAPPDATA%\Programs\usage-sync\venv` and installs `sync/`, writes `%LOCALAPPDATA%\usage-sync\config.toml`, optionally mints the token over SSH into `%LOCALAPPDATA%\usage-sync\token` (never printed), registers logon task `usage-sync` (`pythonw -m usage_sync run`), starts both and prints `usage-sync status`.

`-IngestConnectHost <ip-or-host[:port]>`: use when this PC cannot resolve the ingest host name. The shim then connects to that address while still verifying the certificate for the ingest URL's host; the value stays in the local `config.toml` only. It requires an `https` ingest URL.

Python: the venv is built with a real CPython 3.11-3.13 chosen in this order: `-PythonPath`, `uv python find`, `py -3`, `python`. **Microsoft Store Python is rejected**: it virtualizes `%LOCALAPPDATA%`, so the shim could not see its config, token or CA file. If nothing suitable exists and `uv` is installed, the script runs `uv python install 3.12`; otherwise install Python from python.org or uv first. `-Force` rewrites the config but does not regenerate the token.

Run it from a normal PowerShell window when possible. Started from a process with Windows package identity (for example a terminal embedded in an MSIX-packaged desktop app), new top-level folders under `%LOCALAPPDATA%` are redirected to that app's private storage and Task Scheduler cannot see them (the `usage-sync` task then fails with 0x8007010B). The script detects this and creates `%LOCALAPPDATA%\usage-sync` through a one-shot scheduled task first (waiting up to 10 seconds for the folder to appear; a stale redirected copy elsewhere only produces a warning naming its path). If the package check itself fails, the script warns and assumes a normal process. At the end it checks that the task is running (printing the result code and log tail on failure) and runs `usage-sync once` once.

Fresh profile: `%LOCALAPPDATA%\Programs` may not exist yet. The installer creates it with `New-Item`, which a packaged terminal redirects like any other new top-level folder; run from a normal PowerShell window, or create `%LOCALAPPDATA%\Programs` once from one before installing.

Logs: with no console (`pythonw`), the shim writes a rotating log (1 MB x 3) to `%LOCALAPPDATA%\usage-sync\usage-sync.log`; `usage-sync status` prints the path. Python 3.11+ is required (not the Microsoft Store alias); `tzdata` is installed as a Windows-only dependency (the shim is otherwise stdlib-only). Re-running keeps an existing ActivityWatch install, config and token unless `-Force` (or `-AwVersion` for ActivityWatch).

Flags:

| Flag | Meaning |
| --- | --- |
| `-Device` | Device name shipped with every event (default: lower-cased computer name) |
| `-IngestUrl` | Base URL of the ingest API |
| `-CaFile` | CA bundle that signed the ingest certificate |
| `-IngestConnectHost` | Connect to this `host[:port]` instead of resolving the ingest host name |
| `-PythonPath` | Use this interpreter for the venv. It must be a real 3.11-3.13 outside a virtual environment; if it fails the check the install stops instead of picking another one |
| `-NodeSsh` | `user@host` used only to mint the device token over SSH (validated; the token is never printed) |
| `-AwVersion` | ActivityWatch tag, e.g. `v0.13.2` (default: newest release with a Windows zip) |
| `-RepoPath` | Root of this repository (default: derived from the script location) |
| `-Force` | Overwrite an existing `config.toml` and re-download ActivityWatch |
| `-NoStart` | Register everything but do not start the tasks |
| `-DryRun` | Print each step and change nothing |

`uninstall.ps1` removes both tasks and leaves data in place.

Install the "ActivityWatch Web Watcher" browser extension manually from the browser's extension store.

To bring old `activity-logger` history over, see the "Windows desktop" section in the top-level README.

## Importing old activity-logger history

```powershell
usage-sync import-activity-logger <path-to-sqlite> --device <device-name> --dry-run --until <ISO-datetime-with-offset>
usage-sync import-activity-logger <path-to-sqlite> --device <device-name> --until <ISO-datetime-with-offset>
```

`--until` (timezone-aware, e.g. `2026-11-05T09:00:00+00:00`) imports only rows that start before it: use the first event time of the new client as the cutoff so the overlap is not double-counted. The dry run prints the cutoff and the counts. Re-running is safe (events are idempotent).

Lock-screen and logon processes (`idle_apps`, default `lockapp.exe`, `logonui.exe` and the Linux/macOS equivalents) are shipped as idle window events with a blank title, both live and on import, so they do not count as app usage. Entries in `idle_apps` in `config.toml` are ADDED to the built-in list (case-insensitive exact match, with or without `.exe`); set `idle_apps_replace = true` to use only your list. `compare-activity-logger` uses the same configured list.
