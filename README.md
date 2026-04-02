# Rover Dashboard

Small first iteration of a web-based rover dashboard with a `Flask` backend and a plain HTML, CSS, and JavaScript frontend.

## What it includes

- Login screen with two roles: `admin` and `normal user`
- Dashboard cards for rover connection, connected devices, odometry, and sensors
- Admin-only actions panel
- `Flask` API for login, health, and telemetry
- Jetson-aware telemetry via `jetson-stats` / `jtop` when available
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

On a Jetson board, `jetson-stats` is the preferred telemetry source for board-specific metrics.
If needed, install it with elevated privileges as recommended by the project:

```bash
sudo pip3 install -U jetson-stats
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
  }
}
```

## Next good step

The backend now prefers `jetson-stats` for Jetson-specific monitoring and falls back to generic Linux metrics when `jtop` is unavailable.

Next, we can use the new `jetson` payload in the frontend to render board details like JetPack, power mode, fan state, and accelerator activity the way `jtop` does.
