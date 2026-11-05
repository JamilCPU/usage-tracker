# Windows install

Per-user, no admin rights. Run from a clone of this repo in PowerShell 5.1 or later:

```powershell
# preview first: prints every step, changes nothing
.\install.ps1 -DryRun

.\install.ps1 -Device <device-name> -IngestUrl https://<ingest-host> -CaFile <path-to-ca.pem> -NodeSsh <user>@<node>
```

What it does: downloads the ActivityWatch Windows zip to `%LOCALAPPDATA%\Programs\activitywatch`, registers logon task `ActivityWatch` (`aw-qt.exe`, tray only), builds a venv in `%LOCALAPPDATA%\Programs\usage-sync\venv` and installs `sync/`, writes `%LOCALAPPDATA%\usage-sync\config.toml`, optionally mints the token over SSH into `%LOCALAPPDATA%\usage-sync\token` (never printed), registers logon task `usage-sync` (`pythonw -m usage_sync run`), starts both and prints `usage-sync status`.

`-IngestConnectHost <ip-or-host[:port]>`: use when this PC cannot resolve the ingest host name. The shim then connects to that address while still verifying the certificate for the ingest URL's host; the value stays in the local `config.toml` only. It requires an `https` ingest URL.

Python: the venv is built with a real CPython 3.11-3.13 chosen in this order: `-PythonPath`, `uv python find`, `py -3`, `python`. **Microsoft Store Python is rejected**: it virtualizes `%LOCALAPPDATA%`, so the shim could not see its config, token or CA file. If nothing suitable exists and `uv` is installed, the script runs `uv python install 3.12`; otherwise install Python from python.org or uv first. Python 3.13+ enables strict X.509 checking, which rejects a private CA without a keyUsage extension, so the shim relaxes only that flag when `ca_file` is set (chain and hostname checks stay on). `-Force` rewrites the config but does not regenerate the token.

Run it from a normal PowerShell window when possible. Started from a process with Windows package identity (for example a terminal embedded in an MSIX-packaged desktop app), new top-level folders under `%LOCALAPPDATA%` are redirected to that app's private storage and Task Scheduler cannot see them (the `usage-sync` task then fails with 0x8007010B). The script detects this and creates `%LOCALAPPDATA%\usage-sync` through a one-shot scheduled task first. At the end it checks that the task is running (printing the result code and log tail on failure) and runs `usage-sync once` once.

Logs: with no console (`pythonw`), the shim writes a rotating log (1 MB x 3) to `%LOCALAPPDATA%\usage-sync\usage-sync.log`; `usage-sync status` prints the path. Python 3.11+ is required (not the Microsoft Store alias); `tzdata` is installed as a Windows-only dependency (the shim is otherwise stdlib-only). Re-running keeps an existing ActivityWatch install, config and token unless `-Force` (or `-AwVersion` for ActivityWatch).

Flags: `-AwVersion`, `-RepoPath`, `-Force` (overwrite config), `-NoStart`, `-DryRun`. `uninstall.ps1` removes both tasks and leaves data in place.

Install the "ActivityWatch Web Watcher" browser extension manually from the browser's extension store.

To bring old `activity-logger` history over, see the "Windows desktop" section in the top-level README.
