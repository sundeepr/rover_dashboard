from __future__ import annotations

from datetime import datetime


def get_mock_telemetry() -> dict:
    updated_at = datetime.now().strftime("%H:%M:%S")

    return {
        "status": {
            "battery": "86%",
            "cpuTemp": "58 C",
            "mode": "Teleop Ready",
            "updatedAt": updated_at,
        },
        "devices": [
            {"name": "Stereo Camera", "port": "/dev/video0", "status": "online"},
            {"name": "Lidar", "port": "/dev/ttyUSB0", "status": "online"},
            {"name": "GPS", "port": "/dev/ttyTHS1", "status": "online"},
            {"name": "Motor Controller", "port": "can0", "status": "online"},
        ],
        "odometry": {
            "x": "12.48 m",
            "y": "-3.12 m",
            "heading": "42 deg",
            "speed": "0.84 m/s",
            "wheelTicks": "18234",
            "frame": "odom",
        },
        "sensors": [
            {"name": "IMU", "value": "0.02 g", "detail": "Roll/Pitch stable"},
            {"name": "GPS", "value": "17 sats", "detail": "Fix: RTK float"},
            {"name": "Lidar", "value": "24.6 m", "detail": "Front clearance"},
            {"name": "Ambient", "value": "29.4 C", "detail": "Board enclosure"},
        ],
    }
