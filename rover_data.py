from __future__ import annotations

from datetime import datetime
from pathlib import Path

import psutil


GPU_LOAD_PATHS = (
    Path("/sys/devices/gpu.0/load"),
    Path("/sys/class/devfreq/17000000.ga10b/device/load"),
    Path("/sys/class/devfreq/gpu/load"),
)

THERMAL_ZONE_ROOT = Path("/sys/class/thermal")


def get_mock_telemetry() -> dict:
    updated_at = datetime.now().strftime("%H:%M:%S")

    return {
        "status": {
            "cpuUsage": get_cpu_usage(),
            "memoryUsage": get_memory_usage(),
            "cpuTemp": get_cpu_temperature(),
            "gpuUsage": get_gpu_usage(),
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


def get_cpu_usage() -> str:
    return f"{psutil.cpu_percent(interval=0.2):.1f}%"


def get_memory_usage() -> str:
    memory = psutil.virtual_memory()
    return f"{memory.percent:.1f}%"


def get_cpu_temperature() -> str:
    temperatures = psutil.sensors_temperatures(fahrenheit=False)
    preferred_groups = ("cpu-thermal", "cpu_thermal", "coretemp", "k10temp")

    for group_name in preferred_groups:
        entries = temperatures.get(group_name, [])
        value = extract_temperature(entries)
        if value is not None:
            return format_temperature(value)

    for entries in temperatures.values():
        value = extract_temperature(entries)
        if value is not None:
            return format_temperature(value)

    for zone in sorted(THERMAL_ZONE_ROOT.glob("thermal_zone*")):
        try:
            zone_type = (zone / "type").read_text(encoding="utf-8").strip().lower()
            if "cpu" not in zone_type:
                continue

            raw_value = (zone / "temp").read_text(encoding="utf-8").strip()
            return format_temperature(parse_millicelsius(raw_value))
        except OSError:
            continue

    return "Unavailable"


def get_gpu_usage() -> str:
    for path in GPU_LOAD_PATHS:
        try:
            raw_value = path.read_text(encoding="utf-8").strip()
            return format_gpu_load(raw_value)
        except OSError:
            continue

    return "Unavailable"


def extract_temperature(entries: list) -> float | None:
    for entry in entries:
        current = getattr(entry, "current", None)
        if current is not None:
            return float(current)
    return None


def format_temperature(value: float) -> str:
    return f"{value:.1f} C"


def parse_millicelsius(raw_value: str) -> float:
    value = float(raw_value)
    if value > 1000:
        return value / 1000.0
    return value


def format_gpu_load(raw_value: str) -> str:
    value = float(raw_value)
    if value > 100:
        value = value / 10.0
    return f"{value:.1f}%"
