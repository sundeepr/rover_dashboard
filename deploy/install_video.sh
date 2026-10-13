#!/usr/bin/env bash
# Called by the dashboard installer after its virtual environment is installed.
set -euo pipefail
VIDEO_SOURCE=$1
VIDEO_APP=/opt/rover-dashboard
VIDEO_VERSION=v1.21.2
case $(uname -m) in
    aarch64|arm64) VIDEO_ARCH=arm64; VIDEO_SHA=180a235374a548d9133cdd5e20f5dbd8b01317be1c86e0d7e66df585dc42ed6f ;;
    x86_64) VIDEO_ARCH=amd64; VIDEO_SHA=121be6e00397e473a95d83c75f4fcd3819a6af3b0e1a50889cba43ab4fab6a27 ;;
    *) echo 'Video relay requires Linux arm64 or amd64.' >&2; exit 1 ;;
esac
VIDEO_WORK=$(mktemp -d)
trap 'rm -rf -- "$VIDEO_WORK"' EXIT
curl --fail --location --silent --show-error --retry 3 --max-time 180 \
    "https://github.com/bluenviron/mediamtx/releases/download/$VIDEO_VERSION/mediamtx_${VIDEO_VERSION}_linux_${VIDEO_ARCH}.tar.gz" \
    -o "$VIDEO_WORK/relay.tar.gz"
printf '%s  %s\n' "$VIDEO_SHA" "$VIDEO_WORK/relay.tar.gz" | sha256sum --check --status
tar -xzf "$VIDEO_WORK/relay.tar.gz" -C "$VIDEO_WORK" mediamtx LICENSE
if [[ ! -f /etc/rover-dashboard/cameras.json ]]; then
    if [[ -f "$VIDEO_SOURCE/cameras.local.json" ]]; then
        install -o root -g rover-dashboard -m 0640 "$VIDEO_SOURCE/cameras.local.json" /etc/rover-dashboard/cameras.json
    else
        install -o root -g rover-dashboard -m 0640 "$VIDEO_SOURCE/deploy/cameras.default.json" /etc/rover-dashboard/cameras.json
    fi
fi
"$VIDEO_APP/.venv/bin/python" "$VIDEO_APP/video.py" --output "$VIDEO_WORK/mediamtx.yml"
if ! systemctl is-active --quiet rover-dashboard-video.service; then
    if [[ -n $(ss -H -ltn 'sport = :9001') || -n $(ss -H -lun 'sport = :8189') ]]; then
        echo 'Video relay ports 9001/TCP or 8189/UDP are already in use.' >&2; exit 1
    fi
fi
systemctl stop rover-dashboard-video.service 2>/dev/null || true
install -d -m 0755 "$VIDEO_APP/bin"
install -m 0755 "$VIDEO_WORK/mediamtx" "$VIDEO_APP/bin/mediamtx"
install -m 0644 "$VIDEO_WORK/LICENSE" "$VIDEO_APP/third_party/MediaMTX-LICENSE"
install -m 0644 "$VIDEO_SOURCE/deploy/rover-dashboard-video.service" /etc/systemd/system/rover-dashboard-video.service
systemctl daemon-reload
systemctl enable --now rover-dashboard-video.service
for attempt in {1..15}; do
    if curl --silent --max-time 1 http://127.0.0.1:9001/ >/dev/null; then break; fi
    sleep 1
done
curl --silent --show-error --max-time 2 http://127.0.0.1:9001/ >/dev/null
systemctl is-active --quiet rover-dashboard-video.service
echo 'WebRTC relay enabled. Cameras: /etc/rover-dashboard/cameras.json; media port: 8189/UDP.'
