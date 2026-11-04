#!/usr/bin/env bash
# Install usage-sync into a venv and register its systemd user unit (enabled, not started).
# Re-run after pulling a new version to upgrade.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkg="$(cd "$here/../.." && pwd)"   # the sync/ directory
venv="$HOME/.local/opt/usage-sync"
units="$HOME/.config/systemd/user"

python3 -m venv "$venv"
"$venv/bin/pip" install --quiet --upgrade "$pkg"
mkdir -p "$HOME/.local/bin" "$units"
ln -sf "$venv/bin/usage-sync" "$HOME/.local/bin/usage-sync"
install -m 644 "$here/usage-sync.service" "$units/usage-sync.service"
systemctl --user daemon-reload
systemctl --user enable usage-sync.service
echo "installed. Next: usage-sync init, put the token in ~/.config/usage-sync/token, then:"
echo "  systemctl --user start usage-sync"
