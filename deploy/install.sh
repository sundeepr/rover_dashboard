#!/usr/bin/env bash
# Ubuntu/Jetson: sudo ./install.sh [--console-source /path/to/rover-console]
set -euo pipefail
umask 022
SOURCE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
APP=/opt/rover-dashboard
CONSOLE_SOURCE=
fail() { echo "ERROR: $*" >&2; exit 1; }
while [[ $# -gt 0 ]]; do
    case "$1" in
        --console-source) [[ $# -ge 2 ]] || fail '--console-source needs a path'; CONSOLE_SOURCE=$2; shift 2 ;;
        -h|--help) echo 'sudo ./install.sh [--console-source /path/to/rover-console]'; exit 0 ;;
        *) fail "Unknown argument: $1" ;;
    esac
done
[[ $(uname -s) == Linux && $EUID -eq 0 ]] || fail 'Run with sudo on the Jetson (Ubuntu/Debian).'
[[ -d /run/systemd/system ]] || fail 'A running systemd host is required.'
command -v apt-get >/dev/null || fail 'Ubuntu/Debian apt-get is required.'
[[ "$SOURCE" != "$APP" ]] || fail 'Run from a separate checkout, not /opt/rover-dashboard.'
if [[ -n "$CONSOLE_SOURCE" ]]; then
    [[ -f "$CONSOLE_SOURCE/deploy/install.sh" ]] || fail 'Console checkout does not contain deploy/install.sh.'
    # Only run this explicitly supplied checkout; never clone or alter SensorHub.
    bash "$CONSOLE_SOURCE/deploy/install.sh"
fi
[[ -f /etc/rover-console/Caddyfile && -f /etc/rover-console/web.env ]] || fail 'Install Rover Console first, or pass --console-source /path/to/rover-console.'
command -v caddy >/dev/null || fail 'Caddy is missing; reinstall Rover Console.'
[[ $(systemctl show -p LoadState --value rover-console-web.service) == loaded ]] || fail 'rover-console-web.service is not installed.'
[[ $(systemctl show -p LoadState --value rover-console.service) == loaded ]] || fail 'rover-console.service is not installed.'
apt-get update
apt-get install -y python3 python3-venv python3-pip ca-certificates curl iproute2
python3 -c 'import sys; assert sys.version_info >= (3,10), "Python 3.10+ required (JetPack 6 / Ubuntu 22.04+)"'
if ! systemctl is-active --quiet rover-dashboard.service && [[ -n $(ss -H -ltn 'sport = :9000') ]]; then
    fail 'Port 9000 is already used by another process. Free it before installing.'
fi
WORK=$(mktemp -d /tmp/rover-dashboard-install.XXXXXX)
CONFIG_WRITTEN=false
DASHBOARD_STOPPED=false
cleanup() {
    result=$?
    trap - EXIT
    if [[ $result -ne 0 && "$CONFIG_WRITTEN" == true ]]; then
        echo 'Installation failed; restoring previous console routing and registration.' >&2
        cp -p "$WORK/Caddyfile.before" /etc/rover-console/Caddyfile
        if [[ -f "$WORK/telemetry.before" ]]; then
            cp -p "$WORK/telemetry.before" /etc/rover-console/apps/telemetry.json
        else
            rm -f /etc/rover-console/apps/telemetry.json
        fi
        systemctl restart rover-console-web.service || true
    fi
    if [[ $result -ne 0 && "$DASHBOARD_STOPPED" == true ]]; then
        echo 'Dashboard installation was interrupted; inspect journalctl -u rover-dashboard and rerun the installer.' >&2
    fi
    rm -rf -- "$WORK"
    exit "$result"
}
trap cleanup EXIT
# Prepare and validate console changes BEFORE stopping or replacing anything.
cp -p /etc/rover-console/Caddyfile "$WORK/Caddyfile.original"
python3 "$SOURCE/deploy/register_console.py" --output "$WORK/console"
PASSWORD_HASH=$(sed -n 's/^CONSOLE_PASSWORD_HASH=//p' /etc/rover-console/web.env)
[[ "$PASSWORD_HASH" == '$2'* ]] || fail 'Invalid stored console password hash.'
CONSOLE_PASSWORD_HASH="$PASSWORD_HASH" caddy validate --config "$WORK/console/Caddyfile" --adapter caddyfile
unset PASSWORD_HASH
python3 -m venv "$WORK/venv"
"$WORK/venv/bin/pip" wheel --wheel-dir "$WORK/wheels" -r "$SOURCE/requirements.txt"
getent passwd rover-dashboard >/dev/null || useradd --system --user-group --no-create-home --shell /usr/sbin/nologin rover-dashboard
install -d -m 0755 "$APP" "$APP/vendor" "$APP/dist" "$APP/third_party" /etc/rover-dashboard /etc/rover-console/apps
systemctl stop rover-dashboard.service 2>/dev/null || true
DASHBOARD_STOPPED=true
for file in dashboard_app.py sensorhub_data.py bms_data.py requirements.txt; do
    install -m 0644 "$SOURCE/$file" "$APP/$file"
done
install -m 0644 "$SOURCE/vendor/"*.py "$APP/vendor/"
install -m 0644 "$SOURCE/third_party/JK-BMS-LICENSE" "$APP/third_party/"
for file in index.html app.js styles.css; do install -m 0644 "$SOURCE/$file" "$APP/dist/$file"; done
python3 -m venv "$APP/.venv"
"$APP/.venv/bin/pip" install --no-index --find-links "$WORK/wheels" -r "$SOURCE/requirements.txt"
if [[ ! -f /etc/rover-dashboard/dashboard.env ]]; then
    install -m 0644 "$SOURCE/deploy/dashboard.env" /etc/rover-dashboard/dashboard.env
fi
chown -R root:root "$APP"
chmod -R go-w "$APP"
install -m 0644 "$SOURCE/deploy/rover-dashboard.service" /etc/systemd/system/rover-dashboard.service
systemctl daemon-reload
systemctl enable rover-dashboard.service
systemctl restart rover-dashboard.service
for attempt in {1..20}; do
    if curl --fail --silent --max-time 2 http://127.0.0.1:9000/api/health >/dev/null; then break; fi
    sleep 1
done
curl --fail --silent --show-error --max-time 3 http://127.0.0.1:9000/api/health >/dev/null || fail 'Dashboard did not start; see journalctl -u rover-dashboard.'
DASHBOARD_STOPPED=false
# Keep persistent backups and restore originals automatically if the web restart fails.
BACKUP=/var/backups/rover-dashboard/$(date -u +%Y%m%dT%H%M%SZ)-$$
install -d -m 0700 "$BACKUP"
cp -p /etc/rover-console/Caddyfile "$WORK/Caddyfile.before"
cp -p /etc/rover-console/Caddyfile "$BACKUP/Caddyfile"
if [[ -f /etc/rover-console/apps/telemetry.json ]]; then
    cp -p /etc/rover-console/apps/telemetry.json "$WORK/telemetry.before"
    cp -p /etc/rover-console/apps/telemetry.json "$BACKUP/telemetry.json"
fi
cmp -s "$WORK/Caddyfile.original" /etc/rover-console/Caddyfile || fail 'Console routing changed during installation; rerun to preserve those changes.'
# Recheck registration conflicts immediately before writing.
python3 "$SOURCE/deploy/register_console.py" --output "$WORK/console"
CONFIG_WRITTEN=true
install -m 0644 "$WORK/console/Caddyfile" /etc/rover-console/Caddyfile.telemetry-new
mv /etc/rover-console/Caddyfile.telemetry-new /etc/rover-console/Caddyfile
install -m 0644 "$WORK/console/telemetry.json" /etc/rover-console/apps/telemetry.json
systemctl enable rover-console.service rover-console-web.service
systemctl start rover-console.service
systemctl restart rover-console-web.service
for attempt in {1..20}; do
    code=$(curl --silent --max-time 2 --output /dev/null --write-out '%{http_code}' http://127.0.0.1/telemetry/ || true)
    if [[ "$code" == 401 ]]; then break; fi
    sleep 1
done
[[ "$code" == 401 ]] || fail 'Authenticated console route did not become ready; restoring previous route.'
curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8765/api/rover >/dev/null
systemctl is-active --quiet rover-dashboard.service rover-console.service rover-console-web.service
systemctl is-enabled --quiet rover-dashboard.service rover-console.service rover-console-web.service
CONFIG_WRITTEN=false
printf '\nInstalled and enabled at boot. Open http://<rover-ip>/telemetry/ using your console login.\n'
hostname -I || true
echo "Console configuration backup: $BACKUP"
if ! systemctl is-active --quiet sensorhub-router.service; then
    echo 'SensorHub router is not running: dashboard will show unavailable until SensorHub is started.'
fi
if ! grep -rq 'Open dashboard' /opt/rover-console/frontend/dist; then
    echo 'Dashboard URL works, but the console UI is older and may lack its launch link.'
    echo 'Update the console checkout and rerun with --console-source /path/to/rover-console to rebuild it.'
fi
echo 'Instance names are configured in /etc/rover-dashboard/dashboard.env (existing settings were preserved).'
