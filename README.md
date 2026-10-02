# Rover Dashboard

Small first iteration of a web-based rover dashboard with a `Flask` backend and a plain HTML, CSS, and JavaScript frontend.

## What it includes

- Login screen with two roles: `admin` and `normal user`
- Dashboard cards for rover connection, connected devices, odometry, and sensors
- Admin-only actions panel
- `Flask` API for login, health, and telemetry
- Jetson-aware telemetry via `jetson-stats` / `jtop` when available
- JK-BMS battery telemetry using `syssi/esphome-jk-bms` on an ESP board
- Configurable local rover endpoint
- Automatic fallback to mock telemetry while the Jetson or BMS hardware is not ready

## Demo accounts

- Admin: `admin` / `admin123`
- Normal user: `operator` / `operator123`

## Run locally

Install the backend dependency:

```bash
cd /home/sundeep/workspace/rover_dashboard
pip install -r requirements.txt
```

On a Jetson board, `jetson-stats` is the preferred telemetry source for board-specific metrics.
If needed, install it with elevated privileges as recommended by the project:

```bash
sudo pip3 install -U jetson-stats
```

`jetson-stats` is intentionally not in `requirements.txt` because it is Jetson/Linux-specific and does not install cleanly on macOS.

Start the backend:

```bash
python3 server.py
```

Then open the dashboard:

```text
http://127.0.0.1:6060
```

Battery telemetry defaults to a local mock source, so it works on this machine without the Jetson, ESP board, or BMS connected.

## Direct Jetson Bluetooth battery connection

The backend supports a direct **JK-BMS → Jetson Bluetooth → dashboard** path.
No ESP32 or MQTT broker is needed for this mode. The reader uses `bleak` and
JK02 frame definitions from [syssi/esphome-jk-bms](https://github.com/syssi/esphome-jk-bms).
The upstream Apache-2.0 license is in `third_party/JK-BMS-LICENSE`.

On the Jetson, enable its Bluetooth adapter and install the backend dependencies
in the Python environment used to run the server (`bleak==0.22.3` is already in
`requirements.txt`). The Linux Bluetooth service (BlueZ) must be running.
Find the BMS MAC address using the existing `/api/bms/discover?timeout=6` endpoint
or the Jetson's Bluetooth settings. Close other BMS apps before connecting.

Start the backend with your actual address and protocol, for example:

```bash
BMS_SOURCE=ble BMS_BLE_ADDRESS=AA:BB:CC:DD:EE:FF BMS_BLE_PROTOCOL=JK02_32S python3 server.py
```

For this rover's `24v 45Ah` device (`JK_BD4A8S6P`, hardware `V15H`, firmware
`V15.41`), the supplied configuration selects `JK02_32S` and address
`C8:47:80:44:17:11`. From the repository directory on the Jetson:

```bash
source config/jetson-battery.env
python3 server.py
```

Close the phone's JK app before starting the connection. Open the dashboard to
start telemetry, then check `http://127.0.0.1:6060/api/battery` on the Jetson.
A connected reader reports `available: true` and `source: "jk-bms-ble"`.
The screenshot identifies the hardware; it is not a live connection test.

The address above is a placeholder. Explicitly select the protocol that matches
the BMS hardware: upstream recommends `JK02_24S` for hardware versions 6–10 and
`JK02_32S` for versions 11 and newer. This reader does not support legacy `JK04`.
These names describe protocol layouts, not the number of installed cells.

The worker starts on the first battery/telemetry request. It requests telemetry
only, validates checksums, reconnects after failure, and expires readings after
20 seconds without a valid status frame. The API uses source `jk-bms-ble` for
received data and preserves the actual reception timestamp. Missing configuration,
connection errors, and stale readings show as unavailable, without mock fallback
in BLE mode. Keep one backend process connected to the BMS; the development
reloader is disabled to avoid duplicate Bluetooth connections.

Readings include state of charge, voltage, signed current, calculated power,
cell voltages, temperatures, MOS enable flags, and raw fault codes. Charging state
is based on measured current rather than MOS enable flags. Hardware verification
is still required against the JK app; automated tests use synthetic frames.

Run the protocol tests with `python3 -m unittest discover -s tests -v`.

## Optional ESPHome / MQTT integration

The ESPHome config in `esphome/jk-bms-rover.yaml` uses the JK-BMS external component:

```yaml
external_components:
  - source: github://syssi/esphome-jk-bms@main
```

Later, flash that config to an ESP32 wired to the JK-BMS UART-TTL port. The config publishes MQTT topics under `jk-bms/#`, and the Flask backend can consume those topics.

Create an ESPHome `secrets.yaml` next to the config:

```yaml
wifi_ssid: YOUR_WIFI
wifi_password: YOUR_WIFI_PASSWORD
mqtt_host: 192.168.1.10
mqtt_username: YOUR_MQTT_USER
mqtt_password: YOUR_MQTT_PASSWORD
```

Validate or flash when you have the ESP board:

```bash
esphome config esphome/jk-bms-rover.yaml
esphome run esphome/jk-bms-rover.yaml
```

Use mock battery telemetry locally:

```bash
BMS_SOURCE=mock python3 server.py
```

Use simulator POSTs locally:

```bash
BMS_SOURCE=post python3 server.py
```

Then send a sample battery payload:

```bash
curl -X POST http://127.0.0.1:6060/api/battery/simulate \
  -H 'Content-Type: application/json' \
  -d '{
    "capacity_remaining": 76.4,
    "total_voltage": 52.31,
    "current": -8.2,
    "cells": [3.267, 3.268, 3.269, 3.266, 3.271, 3.268, 3.267, 3.269],
    "power_tube_temperature": 32,
    "temperature_sensor_1": 28,
    "temperature_sensor_2": 29,
    "balancing": true,
    "charging": false,
    "discharging": true,
    "errors": "None"
  }'
```

Use ESPHome MQTT later:

```bash
BMS_SOURCE=mqtt BMS_MQTT_HOST=127.0.0.1 BMS_MQTT_TOPIC_PREFIX=jk-bms python3 server.py
```

Discover BMS-related devices visible to this machine:

```bash
curl http://127.0.0.1:6060/api/bms/discover?timeout=6
```

The discovery endpoint checks ESPHome services on the LAN with mDNS/Bonjour, the LAN ARP cache for nearby IP devices, and BLE advertisements with `bleak`.

Discovery only finds candidates. Actual battery values still come from the ESPHome JK-BMS component over MQTT, or from the simulator/mock sources while hardware is unavailable.

## Backend endpoints

- `GET /api/health`
- `POST /api/login`
- `GET /api/telemetry`
- `GET /api/battery`
- `POST /api/battery/simulate`
- `GET /api/bms/discover?timeout=4`

Example login payload:

```json
{
  "username": "admin",
  "password": "admin123"
}
```

## Telemetry shape

Current response:

```json
{
  "status": {
    "cpuUsage": "23.1%",
    "memoryUsage": "44.2%",
    "cpuTemp": "58.0 C",
    "gpuUsage": "31.0%",
    "updatedAt": "14:32:11",
    "source": "jtop"
  },
  "devices": [
    { "name": "Stereo Camera", "port": "/dev/video0", "status": "online" },
    { "name": "Lidar", "port": "/dev/ttyUSB0", "status": "online" }
  ],
  "odometry": {
    "x": "10.20 m",
    "y": "-2.80 m",
    "heading": "37 deg",
    "speed": "0.74 m/s",
    "wheelTicks": "18234",
    "frame": "odom"
  },
  "sensors": [
    { "name": "Board", "value": "Jetson Orin Nano", "detail": "Detected by jetson-stats" },
    { "name": "JetPack", "value": "6.x / 36.x", "detail": "Software stack" }
  ],
  "jetson": {
    "available": true,
    "source": "jtop",
    "stats": {},
    "board": {}
  },
  "battery": {
    "available": true,
    "source": "mock",
    "status": "Healthy",
    "state": "Discharging",
    "summary": {
      "capacityRemaining": "82.0%",
      "totalVoltage": "52.67 V",
      "current": "-7.50 A",
      "power": "-395 W",
      "cellDelta": "0.016 V"
    },
    "cells": [
      { "index": 1, "voltage": "3.292 V", "rawVoltage": 3.292 }
    ]
  }
}
```

## Next good step

The backend now prefers `jetson-stats` for Jetson-specific monitoring and can read JK-BMS values from mock data, simulator POSTs, or ESPHome MQTT.

For a direct Jetson connection, follow the Bluetooth setup above. The ESP32 UART/MQTT route remains an alternative.

### Section data sources and GPS

The UI greys out sections marked `mock`, `simulator`, or `post` and labels them
as sample data. Unavailable sections show “Awaiting data”. The telemetry payload
uses `status.source` for system metrics, `sources.devices` and `sources.odometry`
for those sections, and `battery.source` / `jetson.source` for hardware readings.
Update each source alongside its real readings when connecting hardware.

The new `gps` object defaults to `available: false` with null readings. When a
rover GPS receiver is integrated, provide `available: true`, a `source` identifying
the receiver, numeric `latitude` and `longitude` in degrees, optional numeric
`altitude` in metres, `satellites`, `fix`, and `updatedAt`. The UI leaves location
fields empty until valid coordinates are available; it does not use the browser's
location or invent a rover position.

The GPS section includes a Google Maps iframe and an “Open in Google Maps” link.
The map shows a world view without a rover marker when GPS is unavailable.
Valid coordinates centre the map on the rover and reveal the external map link. The frame only reloads when coordinates change (rounded to six
decimal places); mock positions retain the section's grey sample styling.
