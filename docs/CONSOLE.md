# Host Rover Dashboard under Rover Console

The dashboard is a separate unprivileged app, not part of the console process.
Browser `/telemetry/` → authenticated Caddy → loopback `127.0.0.1:9000`.
Caddy strips the prefix; the backend serves `/`, `/api/...`, and `/ws`.
The existing vanilla frontend is built by copying its three assets; no Vite or
client router is used. `<base href="/telemetry/">` handles assets at nested URLs.

## Install on the Jetson

Rover Console must already be installed. SensorHub supplies telemetry and can be
started separately; the dashboard shows unavailable if it is absent.

```sh
git clone https://github.com/sundeepr/rover_dashboard.git
cd rover_dashboard
sudo ./install.sh
```

To also rebuild/reinstall an updated Rover Console checkout for its launch link:

```sh
sudo ./install.sh --console-source /path/to/rover-console
```

The installer installs OS/Python dependencies, builds the vanilla frontend,
creates the unprivileged dashboard account, installs the loopback service, adds
`/telemetry/` to the installed Caddyfile, creates the console app registration,
and enables dashboard/console/web services at boot. It validates the candidate
Caddyfile before touching the live file and keeps backups under
`/var/backups/rover-dashboard/`. A failed console restart/check restores the
previous route and registration. Rerunning updates the dashboard and preserves
`/etc/rover-dashboard/dashboard.env`. Existing custom telemetry routes or
registrations are rejected for review, not silently replaced. Failed dashboard
code/dependency installation is reported and requires rerunning; it does not roll
back an old dashboard release.

Requirements: Ubuntu/Debian with systemd, Python 3.10+ (JetPack 6 / Ubuntu 22.04+
recommended), and network access to apt/PyPI. The console installer is only run
when `--console-source` is explicitly supplied. An older installed console can
still serve the direct `/telemetry/` URL; rebuild the updated console source to
show the launch link in its UI.

Stop any old standalone dashboard process holding the Bluetooth device. Configure
hardware only in SensorHub. If its instance names differ from `battery`, `gps`,
`jetson`, `imu`, and `controller`, edit `/etc/rover-dashboard/dashboard.env` and restart:

```sh
sudo systemctl restart rover-dashboard
```

Existing Caddy authentication covers all telemetry routes, including WebSocket upgrades.
The dashboard has no second demo login. The backend binds only loopback and should
not be exposed directly. HTTP follows the console's existing local-network setup.

## GPS and IMU

The dashboard reads `hub/sensors/gps/fix` (NMEA GPS) and
`hub/sensors/imu/data` (BNO08x), plus each instance's driver status heartbeat.
The GPS map uses valid fresh coordinates; without a fix it keeps the world view
and shows available satellite/fix diagnostics. To avoid jitter, the map recenters
only after at least 10 metres of movement from its last displayed location and
at least 15 seconds since its last update. Coordinates and the external Maps
link still update on every reading; the first valid fix displays immediately. GPS speed is in m/s and course
is in degrees. The IMU panel displays roll/pitch/yaw, heading, quaternion,
acceleration, angular velocity, magnetic field, gravity and calibration accuracy.
Optional missing readings display `--`; stale data clears the live readings.

For different SensorHub instance names, set these in
`/etc/rover-dashboard/dashboard.env` (names are instance IDs, not driver types):

```sh
SENSORHUB_GPS_INSTANCE=gps
SENSORHUB_IMU_INSTANCE=imu
```

Restart `rover-dashboard` after changes. SensorHub retains all serial/I2C access;
the dashboard only subscribes to messages. Check the `gps` and `imu` objects in
`/telemetry/api/telemetry` for availability and diagnostic reasons.

## STM32 controller

The read-only controller panel subscribes to `hub/sensors/controller/telemetry`
and `hub/meta/drivers/controller/status`. It shows arming, autonomy, host command
freshness, RC/host inputs, left/right motor commands, pump command, wheel base,
board time and extra fields. A read-only joystick plots `rc.steer` horizontally
and `rc.throttle` vertically on their −100 to +100 scales. The marker hides when
RC readings are missing, stale or out of range; numeric readings remain beside it.
Null values display `--`; zero commands stay `0`.
Motor and pump values are reported commands, not measured motion or pump feedback.
A stale host-command flag is shown separately from stale SensorHub telemetry.

For a different instance ID, set `SENSORHUB_CONTROLLER_INSTANCE=controller` in
`/etc/rover-dashboard/dashboard.env` and restart `rover-dashboard`. Existing
installations default to `controller` without needing an environment-file edit.
Controller data expires after two seconds and also requires a running heartbeat
received within five seconds. The panel clears readings when unavailable and
never sends commands to the controller.

## Acceptance on the rover

- Sign into `http://<rover-ip>/`, select Rover Dashboard, choose Open dashboard.
- `/telemetry` redirects to `/telemetry/`; `/telemetry/overview` refreshes correctly.
- Developer tools: assets load from `/telemetry/`; requests use
  `/telemetry/api/telemetry` and WebSocket `/telemetry/ws` (101 upgrade).
- `/api/rover` still returns the console identity, and
  `/telemetry/api/health` identifies `rover-dashboard`.
- Missing assets and unknown API routes return 404, not the frontend HTML.
- A disconnected SensorHub displays unavailable data, never mock values. Battery
  expires at 20 seconds, GPS at 5, Jetson at 3, IMU at 2; all require a running
  heartbeat received within 5 seconds. GPS without a fix keeps the world map.
- `systemctl is-enabled rover-dashboard rover-console rover-console-web` and
  `systemctl is-active rover-dashboard rover-console rover-console-web` succeed.
  Reboot the Jetson and repeat the browser and service checks.

The current dashboard does not derive odometry from GPS or IMU; odometry remains
unavailable until SensorHub provides an agreed stream. SensorHub simulation should
be disabled on a live rover; simulated battery messages are labeled simulated.

## Local verification

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
python3 scripts/build_frontend.py
.venv/bin/python -m unittest discover -s tests
.venv/bin/gunicorn --bind 127.0.0.1:9000 --workers 1 --threads 32 dashboard_app:app
```

Use a Caddy prefix route for browser verification; direct backend `/` intentionally
emits browser asset URLs under `/telemetry/`. The legacy hardware modules remain
as reference/test code but are never imported by the running service except
`bms_data.py`'s pure payload formatting helpers; `get_battery_snapshot` is not called.

The supported single-file SensorHub client was copied from commit `de183ba` into
`vendor/sensorhub_client.py` as directed by SensorHub's integration guide. No changes
were made to SensorHub itself. Request handlers read its cache without waiting for
a hardware message. A background connector retries when the bus is unavailable.
