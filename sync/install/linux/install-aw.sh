#!/usr/bin/env bash
# Install ActivityWatch (AppImage, extracted: no FUSE needed) as systemd user services.
# Usage: install-aw.sh            (AW_VERSION=v0.14.1 by default)
set -euo pipefail

AW_VERSION="${AW_VERSION:-v0.14.1}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
opt="$HOME/.local/opt/activitywatch"
units="$HOME/.config/systemd/user"
url="https://github.com/ActivityWatch/activitywatch/releases/download/$AW_VERSION/activitywatch-linux-x86_64.AppImage"

mkdir -p "$opt" "$units"
cd "$opt"
if [ ! -f activitywatch.AppImage ]; then
  curl -fL --retry 3 -o activitywatch.AppImage.part "$url"
  mv activitywatch.AppImage.part activitywatch.AppImage
fi
chmod +x activitywatch.AppImage
rm -rf squashfs-root
./activitywatch.AppImage --appimage-extract >/dev/null

find_bin() {
  local found
  found="$(find "$opt/squashfs-root" -type f -name "$1" -perm -u+x | head -n 1)"
  if [ -z "$found" ]; then
    echo "error: $1 not found under $opt/squashfs-root" >&2
    exit 1
  fi
  echo "$found"
}
aw_server="$(find_bin aw-server-rust)"
aw_afk="$(find_bin aw-watcher-afk)"
aw_window="$(find_bin aw-watcher-window)"

# Display for the X11 watchers: $DISPLAY, else the first X socket.
display="${DISPLAY:-}"
if [ -z "$display" ]; then
  sock="$(find /tmp/.X11-unix -maxdepth 1 -name 'X*' 2>/dev/null | sort | head -n 1)"
  [ -n "$sock" ] && display=":${sock##*/X}"
fi
env_lines=""
if [ -n "$display" ]; then
  env_lines="Environment=DISPLAY=$display"
else
  echo "warning: no X display detected; window watcher will not work" >&2
fi
if [ -f "$HOME/.Xauthority" ]; then
  env_lines="${env_lines}"$'\n'"Environment=XAUTHORITY=%h/.Xauthority"
fi
env_lines="$(printf '%s' "$env_lines" | sed '/^$/d')"

render() {  # render <template> <dest>
  python3 - "$1" "$2" "$aw_server" "$aw_afk" "$aw_window" "$env_lines" <<'PY'
import sys
src, dst, server, afk, window, env = sys.argv[1:7]
text = open(src, encoding="utf-8").read()
text = text.replace("@AW_SERVER@", server).replace("@AW_AFK@", afk).replace("@AW_WINDOW@", window)
text = text.replace("@ENV@\n", env + "\n" if env else "")
open(dst, "w", encoding="utf-8").write(text)
PY
}
for unit in aw-server aw-watcher-afk aw-watcher-window; do
  render "$here/$unit.service" "$units/$unit.service"
done

systemctl --user daemon-reload
systemctl --user enable --now aw-server.service aw-watcher-afk.service aw-watcher-window.service
loginctl enable-linger "$USER"

for _ in $(seq 1 20); do
  if curl -fsS http://127.0.0.1:5600/api/0/info; then
    echo
    echo "ActivityWatch is up."
    exit 0
  fi
  sleep 1
done
echo "error: aw-server did not answer on 127.0.0.1:5600" >&2
exit 1
