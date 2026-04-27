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

## JK-BMS integration

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

Next, wire the ESP32 to the real JK-BMS UART-TTL port and point `BMS_SOURCE=mqtt` at the MQTT broker used by ESPHome.
