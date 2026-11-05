#!/usr/bin/env bash
# Install usage-sync into a venv and register its systemd user unit (enabled, not started).
# Re-run after pulling a new version to upgrade.
# Usage: install-sync.sh [--instance NAME]
#   --instance NAME  also set up a pull instance (usage-sync@NAME) that reads a remote
#                    ActivityWatch server, with its own config dir ~/.config/usage-sync-NAME/
set -euo pipefail

instance=""
case "${1:-}" in
  "") ;;
  --instance)
    instance="${2:-}"
    if ! [[ "$instance" =~ ^[a-z0-9-]+$ ]]; then
      echo "error: --instance needs a name matching ^[a-z0-9-]+\$" >&2
      exit 2
    fi
    ;;
  *)
    echo "usage: $0 [--instance NAME]" >&2
    exit 2
    ;;
esac

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pkg="$(cd "$here/../.." && pwd)"   # the sync/ directory
venv="$HOME/.local/opt/usage-sync"
units="$HOME/.config/systemd/user"

python3 -m venv "$venv"
"$venv/bin/pip" install --quiet --upgrade "$pkg"
mkdir -p "$HOME/.local/bin" "$units"
ln -sf "$venv/bin/usage-sync" "$HOME/.local/bin/usage-sync"
install -m 644 "$here/usage-sync.service" "$units/usage-sync.service"
if [ -n "$instance" ]; then
  install -m 644 "$here/usage-sync@.service" "$units/usage-sync@.service"
  conf="$HOME/.config/usage-sync-$instance"
  mkdir -p "$conf"
  chmod 700 "$conf"
fi
systemctl --user daemon-reload
systemctl --user enable usage-sync.service
if [ -n "$instance" ]; then
  systemctl --user enable "usage-sync@$instance.service"
  systemctl --user try-restart "usage-sync@$instance"
fi
systemctl --user try-restart usage-sync
if [ -n "$instance" ]; then
  echo "installed instance '$instance' (enabled, not started). Next:"
  echo "  usage-sync --config $conf/config.toml init --device <device> --ingest-url https://<ingest-host> --aw-url http://<host>:5600"
  echo "  ( umask 077; mint-token.sh <device> usage > $conf/token )"
  echo "  systemctl --user start usage-sync@$instance"
else
  echo "installed. Next: usage-sync init, put the token in ~/.config/usage-sync/token, then:"
  echo "  systemctl --user start usage-sync"
fi
# NOTE: if you edit ExecStart, the -v flag must come BEFORE the subcommand (usage-sync -v run).
