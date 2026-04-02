# Rover Dashboard

Small first iteration of a web-based rover dashboard with a `Flask` backend and a plain HTML, CSS, and JavaScript frontend.

## What it includes

- Login screen with two roles: `admin` and `normal user`
- Dashboard cards for rover connection, connected devices, odometry, and sensors
- Admin-only actions panel
- `Flask` API for login, health, and telemetry
- Configurable local rover endpoint
- Automatic fallback to mock telemetry while the Jetson API is not ready

## Demo accounts

- Admin: `admin` / `admin123`
- Normal user: `operator` / `operator123`

## Run locally

Install the backend dependency:

```bash
cd /home/sundeep/workspace/rover_dashboard
pip install -r requirements.txt
```

Start the backend:

```bash
python3 server.py
```

Then open the dashboard:

```text
http://127.0.0.1:6060
```

## Backend endpoints

- `GET /api/health`
- `POST /api/login`
- `GET /api/telemetry`

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
    "battery": "82%",
    "cpuTemp": "61 C",
    "mode": "Autonomous",
    "updatedAt": "14:32:11"
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
    { "name": "IMU", "value": "0.03 g", "detail": "Stable" },
    { "name": "GPS", "value": "16 sats", "detail": "RTK fixed" }
  ]
}
```

## Next good step

The system health card now reads CPU usage, memory usage, CPU temperature, and GPU usage from Python. On Jetson, GPU usage is read from common sysfs paths when available.

Next, we can replace the remaining mock values in `rover_data.py` with real Jetson device scans, ROS topic data, or serial/CAN status.
