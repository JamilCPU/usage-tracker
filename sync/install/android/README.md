# Android phone

The phone is tracked by **pull**: the ActivityWatch Android app runs its own server on the phone, and a second `usage-sync` instance on the Linux desktop reads that server over the network and ships the events to the Lab Data Store under the phone's device name and its own token. Nothing but the ActivityWatch app is installed on the phone.

Only the **foreground app** is tracked. There are no browser tabs on Android (Chrome has no extension support); events carry the app's package name.

## Phone

1. Install the ActivityWatch Android app (F-Droid or Play Store).
2. Open it and grant **Usage Access** when asked, and leave battery optimisation off for it so it keeps running.
3. Give the phone a stable address: a DHCP reservation in the router (done by the owner). The phone must be reachable from the Linux desktop.
4. From the desktop, check the phone's server (unverified against the current Android app: some versions listen on localhost only, see the fallback below):

   ```bash
   curl http://<phone>:5600/api/0/info
   ```

## Pull instance on the Linux desktop

From a clone of this repo, as the desktop user:

```bash
# 1. Install usage-sync, the template unit usage-sync@.service and ~/.config/usage-sync-phone/ (enabled, not started)
sync/install/linux/install-sync.sh --instance phone

# 2. Config for this instance
usage-sync --config ~/.config/usage-sync-phone/config.toml init \
  --device <phone> --ingest-url https://ingest.lab \
  --aw-url http://<phone>:5600 --ca-file <lab-ca.pem>

# 3. Token for the phone's device name. mint-token.sh lives in the lab-data-store repo on the
#    cluster node, so mint it there over SSH and write it into this instance's directory:
( umask 077; ssh <node> 'bash ~/src/lab-data-store/scripts/mint-token.sh <phone> usage' > ~/.config/usage-sync-phone/token )
#    If this desktop IS the cluster node, run the script directly:
( umask 077; bash ~/src/lab-data-store/scripts/mint-token.sh <phone> usage > ~/.config/usage-sync-phone/token )

# 4. Start, then verify
systemctl --user start usage-sync@phone
usage-sync --config ~/.config/usage-sync-phone/config.toml status    # shows the aw url
usage-sync --config ~/.config/usage-sync-phone/config.toml once
```

`install-sync.sh --instance` leaves the plain `usage-sync.service` enabled. That is right when this machine also runs a local ActivityWatch; if it does not, disable it with `systemctl --user disable --now usage-sync`.

State (cursors) and the token live next to the config file, so instances do not interfere. For a slow link raise `aw_timeout_seconds` (default 10) in the config.

When the phone is away from home it is unreachable: the instance reports the error each cycle and backs off (up to 600 s) without moving its cursors. ActivityWatch on the phone keeps the events, and the next successful pull ships them with their original timestamps.

## Fallback: the app listens on localhost only

This Termux route is untested.

If a version of the app only listens on `127.0.0.1`, the desktop cannot reach it. Run the shim on the phone itself instead (not provided by this repo, outline only):

1. Install Termux and Termux:Boot; in Termux run `pkg install python`.
2. Copy the `sync/` directory to the phone and `pip install` it.
3. Create `~/.config/usage-sync/config.toml` with the phone's device name, the ingest URL (and CA file, copied over) and `aw_url = "http://127.0.0.1:5600"`; store the token next to it.
4. Add a Termux:Boot script in `~/.termux/boot/` that runs `usage-sync run`.
5. The phone then needs a route to the ingest API itself (same LAN, or a VPN when away).
