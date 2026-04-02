from __future__ import annotations

from datetime import datetime
from pathlib import Path
import re
from typing import Any

import psutil

try:
    from jtop import jtop
except ImportError:
    jtop = None


GPU_LOAD_PATHS = (
    Path("/sys/devices/gpu.0/load"),
    Path("/sys/class/devfreq/17000000.ga10b/device/load"),
    Path("/sys/class/devfreq/gpu/load"),
)

THERMAL_ZONE_ROOT = Path("/sys/class/thermal")

_JETSON_CLIENT = None
DISPLAY_STAT_KEYS = (
    "APE",
    "CPU1",
    "CPU2",
    "CPU3",
    "CPU4",
    "CPU5",
    "CPU6",
    "EMC",
    "Fan pwmfan0",
    "GPU",
    "Power TOT",
    "Power VDD_CPU_GPU_CV",
    "Power VDD_SOC",
    "RAM",
    "SWAP",
    "Temp cpu",
    "Temp gpu",
    "Temp soc0",
    "Temp soc1",
    "Temp soc2",
    "Temp tj",
    "nvp model",
    "time",
    "uptime",
)


def get_mock_telemetry() -> dict:
    updated_at = datetime.now().strftime("%H:%M:%S")
    jetson_snapshot = read_jetson_snapshot()

    return {
        "status": build_status(updated_at, jetson_snapshot),
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
        "sensors": build_sensor_cards(jetson_snapshot),
        "jetson": build_jetson_payload(jetson_snapshot),
    }


def build_status(updated_at: str, jetson_snapshot: dict | None) -> dict:
    cpu_usage = get_cpu_usage()
    memory_usage = get_memory_usage()
    cpu_temp = get_cpu_temperature()
    gpu_usage = get_gpu_usage()
    source = "system"

    if jetson_snapshot:
        stats = jetson_snapshot.get("stats", {})
        normalized_stats = normalize_keys(stats)
        extracted_cpu = extract_jetson_cpu_usage(stats)
        extracted_memory = extract_jetson_memory_usage(normalized_stats)
        extracted_temp = extract_jetson_cpu_temp(jetson_snapshot)
        extracted_gpu = extract_jetson_gpu_usage(normalized_stats)

        cpu_usage = format_percent(extracted_cpu) or cpu_usage
        memory_usage = format_percent(extracted_memory) or memory_usage
        cpu_temp = format_temperature(extracted_temp) if extracted_temp is not None else cpu_temp
        gpu_usage = format_percent(extracted_gpu) or gpu_usage
        source = "jtop"

    return {
        "cpuUsage": cpu_usage,
        "memoryUsage": memory_usage,
        "cpuTemp": cpu_temp,
        "gpuUsage": gpu_usage,
        "updatedAt": updated_at,
        "source": source,
    }


def build_sensor_cards(jetson_snapshot: dict | None) -> list[dict]:
    if not jetson_snapshot:
        return [
            {"name": "IMU", "value": "0.02 g", "detail": "Roll/Pitch stable"},
            {"name": "GPS", "value": "17 sats", "detail": "Fix: RTK float"},
            {"name": "Lidar", "value": "24.6 m", "detail": "Front clearance"},
            {"name": "Ambient", "value": "29.4 C", "detail": "Board enclosure"},
        ]

    board = jetson_snapshot.get("board", {})
    info = normalize_keys(flatten_text_fields(board))
    stats = normalize_keys(jetson_snapshot.get("stats", {}))
    cards = []

    model = first_non_empty(
        info.get("hardware.model"),
        info.get("hardware.module"),
        info.get("platform.machine"),
        info.get("system.type"),
    )
    if model:
        cards.append({"name": "Board", "value": model, "detail": "Detected by jetson-stats"})

    software = " / ".join(
        value for value in [info.get("hardware.jetpack"), info.get("hardware.l4t")] if value
    )
    if software:
        cards.append({"name": "JetPack", "value": software, "detail": "Software stack"})

    power_mode = first_non_empty(stringify_nvpmodel(jetson_snapshot.get("nvpmodel")), info.get("hardware.power"))
    if power_mode:
        cards.append({"name": "Power Mode", "value": power_mode, "detail": "Current Jetson power profile"})

    fan_state = stringify_fan(jetson_snapshot.get("fan"))
    if fan_state and fan_state != "Unavailable":
        cards.append({"name": "Fan", "value": fan_state, "detail": "Fan telemetry from jetson-stats"})

    total_power = parse_numeric(stats.get("power tot"))
    if total_power is not None:
        cards.append(
            {
                "name": "Power",
                "value": f"{total_power:.0f} mW",
                "detail": "Total board power draw",
            }
        )

    engines = stringify_engines(jetson_snapshot.get("engine"))
    if engines and engines != "Unavailable":
        cards.append({"name": "Engines", "value": trim_text(engines, 24), "detail": "Jetson accelerator activity"})

    if cards:
        return cards[:4]

    return [
        {"name": "Jetson", "value": "Connected", "detail": "jetson-stats is available"},
        {"name": "CPU", "value": get_cpu_usage(), "detail": "Fallback CPU reading"},
        {"name": "Memory", "value": get_memory_usage(), "detail": "Fallback memory reading"},
        {"name": "GPU", "value": get_gpu_usage(), "detail": "Fallback GPU reading"},
    ]


def build_jetson_payload(jetson_snapshot: dict | None) -> dict:
    if not jetson_snapshot:
        return {
            "available": False,
            "source": "system",
            "error": "jetson-stats not available",
        }

    return {
        "available": True,
        "source": "jtop",
        "stats": jetson_snapshot.get("stats", {}),
        "board": jetson_snapshot.get("board", {}),
        "memory": jetson_snapshot.get("memory", {}),
        "fan": jetson_snapshot.get("fan"),
        "engine": jetson_snapshot.get("engine"),
        "nvpmodel": jetson_snapshot.get("nvpmodel"),
        "jetsonClocks": jetson_snapshot.get("jetson_clocks"),
        "summary": {
            "model": extract_board_model(jetson_snapshot.get("board", {})),
            "jetpack": extract_software_stack(jetson_snapshot.get("board", {})),
            "fan": stringify_fan(jetson_snapshot.get("fan")),
            "powerMode": stringify_nvpmodel(jetson_snapshot.get("nvpmodel")),
        },
        "details": build_detailed_stats(jetson_snapshot.get("stats", {})),
        "memoryUsage": build_memory_usage(jetson_snapshot),
    }


def read_jetson_snapshot() -> dict | None:
    if jtop is None:
        return None

    global _JETSON_CLIENT

    try:
        if _JETSON_CLIENT is None:
            _JETSON_CLIENT = jtop()
            _JETSON_CLIENT.start()

        stats = sanitize_for_json(getattr(_JETSON_CLIENT, "stats", {}) or {})
        board = sanitize_for_json(safe_getattr(_JETSON_CLIENT, "board", {}) or {})

        return {
            "stats": stats,
            "board": board,
            "memory": sanitize_for_json(safe_getattr(_JETSON_CLIENT, "memory", {})),
            "fan": sanitize_for_json(safe_getattr(_JETSON_CLIENT, "fan", {})),
            "engine": sanitize_for_json(safe_getattr(_JETSON_CLIENT, "engine", {})),
            "nvpmodel": sanitize_for_json(safe_getattr(_JETSON_CLIENT, "nvpmodel", {})),
            "jetson_clocks": sanitize_for_json(safe_getattr(_JETSON_CLIENT, "jetson_clocks", {})),
        }
    except Exception:
        close_jetson_client()
        return None


def close_jetson_client() -> None:
    global _JETSON_CLIENT

    if _JETSON_CLIENT is None:
        return

    try:
        _JETSON_CLIENT.close()
    except Exception:
        pass

    _JETSON_CLIENT = None


def get_cpu_usage() -> str:
    return f"{psutil.cpu_percent(interval=0.2):.1f}%"


def get_memory_usage() -> str:
    memory = psutil.virtual_memory()
    return f"{memory.percent:.1f}%"


def get_cpu_temperature() -> str:
    temperatures = read_psutil_temperatures()
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
            parsed_value = parse_millicelsius(raw_value)
            if parsed_value is None:
                continue
            return format_temperature(parsed_value)
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


def extract_jetson_cpu_usage(stats: dict) -> float | None:
    cpu_block = first_non_empty(stats.get("CPU"), stats.get("cpu"))
    averaged_cpu = average_numeric_values(cpu_block)
    if averaged_cpu is not None:
        return averaged_cpu

    core_values = [
        coerce_percent(value)
        for key, value in stats.items()
        if re.fullmatch(r"cpu\d+", str(key).lower())
    ]
    numeric_values = [value for value in core_values if value is not None]
    if numeric_values:
        return sum(numeric_values) / len(numeric_values)

    return None


def extract_jetson_memory_usage(stats: dict) -> float | None:
    ram_block = first_non_empty(stats.get("ram"), stats.get("mem"))
    percent = coerce_percent(ram_block)
    if percent is None and isinstance(ram_block, (int, float)):
        return float(ram_block) * 100.0
    return percent


def extract_jetson_cpu_temp(jetson_snapshot: dict) -> float | None:
    stats = normalize_keys(jetson_snapshot.get("stats", {}))

    direct_cpu_temp = coerce_temperature(stats.get("temp cpu"))
    if direct_cpu_temp is not None and direct_cpu_temp > -200:
        return direct_cpu_temp

    for key, value in stats.items():
        key_name = str(key).lower()
        if "temp" in key_name and "cpu" in key_name:
            parsed = coerce_temperature(value)
            if parsed is not None and parsed > -200:
                return parsed

    board = jetson_snapshot.get("board", {})
    flattened = normalize_keys(flatten_text_fields(board))
    for key, value in flattened.items():
        key_name = key.lower()
        if "temp" in key_name and "cpu" in key_name:
            parsed = coerce_temperature(value)
            if parsed is not None and parsed > -200:
                return parsed

    return None


def extract_jetson_gpu_usage(stats: dict) -> float | None:
    direct_gpu = coerce_percent(stats.get("gpu"))
    if direct_gpu is not None:
        return direct_gpu

    gpu_values = [
        coerce_percent(value)
        for key, value in stats.items()
        if str(key).lower().startswith("gpu")
    ]
    numeric_values = [value for value in gpu_values if value is not None]
    if numeric_values:
        return sum(numeric_values) / len(numeric_values)

    return None


def average_numeric_values(value: Any) -> float | None:
    if isinstance(value, dict):
        numbers = [coerce_percent(item) for item in value.values()]
    elif isinstance(value, (list, tuple)):
        numbers = [coerce_percent(item) for item in value]
    else:
        return coerce_percent(value)

    numeric_values = [number for number in numbers if number is not None]
    if not numeric_values:
        return None

    return sum(numeric_values) / len(numeric_values)


def build_detailed_stats(stats: dict) -> list[dict]:
    details = []

    for key in DISPLAY_STAT_KEYS:
        if key in stats:
            details.append(
                {
                    "name": key,
                    "value": format_detail_value(key, stats.get(key)),
                }
            )

    return details


def build_memory_usage(jetson_snapshot: dict) -> dict:
    memory = psutil.virtual_memory()
    ram = {
        "percent": f"{memory.percent:.1f}%",
        "used": format_bytes(memory.used),
        "total": format_bytes(memory.total),
        "detail": f"{format_bytes(memory.used)} / {format_bytes(memory.total)}",
    }

    gpu_memory = {
        "available": False,
        "value": "Shared with system RAM",
        "detail": "Jetson uses unified memory, so this payload does not expose separate GPU VRAM usage.",
    }

    raw_jtop_memory = normalize_keys(jetson_snapshot.get("memory", {}))
    jtop_memory = raw_jtop_memory if isinstance(raw_jtop_memory, dict) else {}

    possible_gpu_memory = first_non_empty(
        jtop_memory.get("gpu"),
        jtop_memory.get("vram"),
        jtop_memory.get("nvmap"),
        jtop_memory.get("iram"),
    )

    if possible_gpu_memory in (None, "", {}, []) and raw_jtop_memory not in (None, "", {}, []):
        gpu_memory = {
            "available": True,
            "value": stringify_value(raw_jtop_memory),
            "detail": "Raw memory payload reported by jetson-stats",
        }

    if possible_gpu_memory not in (None, "", {}, []):
        gpu_memory = {
            "available": True,
            "value": stringify_value(possible_gpu_memory),
            "detail": "Reported by jetson-stats",
        }

    return {
        "ram": ram,
        "gpu": gpu_memory,
    }


def normalize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key).strip().lower(): normalize_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_keys(item) for item in value]
    return value


def format_detail_value(key: str, value: Any) -> str:
    key_name = key.lower()

    if key_name == "ram":
        percent = coerce_percent(value)
        if percent is not None:
            return f"{percent * 100.0:.1f}%" if 0 <= percent <= 1 else f"{percent:.1f}%"

    if key_name.startswith("temp "):
        parsed = coerce_temperature(value)
        if parsed is not None and parsed > -200:
            return format_temperature(parsed)

    if key_name.startswith("power "):
        parsed = parse_numeric(value)
        if parsed is not None:
            return f"{parsed:.0f} mW"

    if key_name.startswith("cpu") or key_name in {"gpu", "emc", "swap", "fan pwmfan0"}:
        percent = coerce_percent(value)
        if percent is not None:
            return f"{percent * 100.0:.1f}%" if 0 <= percent <= 1 else f"{percent:.1f}%"

    return stringify_value(value)


def coerce_percent(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        if value > 100 and value <= 1000:
            return value / 10.0
        return float(value)

    if isinstance(value, dict):
        for key in ("val", "value", "use", "used", "percent", "online"):
            parsed = coerce_percent(value.get(key))
            if parsed is not None:
                return parsed

        used = parse_numeric(value.get("used"))
        total = parse_numeric(value.get("total"))
        if used is not None and total:
            return (used / total) * 100.0

        return None

    if isinstance(value, str):
        if "/" in value:
            left, _, right = value.partition("/")
            used = parse_numeric(left)
            total = parse_numeric(right)
            if used is not None and total:
                return (used / total) * 100.0

        parsed = parse_numeric(value)
        if parsed is None:
            return None
        if parsed > 100 and parsed <= 1000:
            return parsed / 10.0
        return parsed

    return None


def coerce_temperature(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, dict):
        for key in ("temp", "value", "val", "current"):
            parsed = coerce_temperature(value.get(key))
            if parsed is not None:
                return parsed
        return None

    if isinstance(value, str):
        return parse_numeric(value)

    return None


def extract_temperature(entries: list) -> float | None:
    for entry in entries:
        current = getattr(entry, "current", None)
        if current is not None:
            return float(current)
    return None


def format_temperature(value: float) -> str:
    return f"{value:.1f} C"


def format_percent(value: float | None) -> str | None:
    if value is None:
        return None
    if 0 <= value <= 1:
        value *= 100.0
    return f"{max(0.0, min(value, 100.0)):.1f}%"


def parse_millicelsius(raw_value: str) -> float | None:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return None

    if value > 1000:
        return value / 1000.0
    return value


def format_gpu_load(raw_value: str) -> str:
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        return "Unavailable"

    if value > 100:
        value = value / 10.0
    return f"{value:.1f}%"


def read_psutil_temperatures() -> dict:
    try:
        return psutil.sensors_temperatures(fahrenheit=False)
    except (AttributeError, OSError, TypeError, ValueError):
        return {}


def safe_getattr(obj: Any, attr_name: str, default: Any = None) -> Any:
    try:
        return getattr(obj, attr_name)
    except Exception:
        return default


def sanitize_for_json(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value

    if isinstance(value, dict):
        return {str(key): sanitize_for_json(item) for key, item in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [sanitize_for_json(item) for item in value]

    return str(value)


def flatten_text_fields(value: Any, prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}

    if isinstance(value, dict):
        for key, item in value.items():
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            flattened.update(flatten_text_fields(item, child_prefix))
        return flattened

    if isinstance(value, (list, tuple, set)):
        text_value = ", ".join(str(item) for item in value if item is not None)
        if prefix and text_value:
            flattened[prefix] = text_value
        return flattened

    text_value = stringify_value(value)
    if prefix and text_value and text_value != "Unavailable":
        flattened[prefix] = text_value

    return flattened


def stringify_value(value: Any) -> str:
    if value is None:
        return "Unavailable"

    if isinstance(value, bool):
        return "On" if value else "Off"

    if isinstance(value, (int, float)):
        return str(value)

    if isinstance(value, str):
        text = value.strip()
        return text or "Unavailable"

    if isinstance(value, dict):
        preferred_parts = []
        for key in ("name", "model", "status", "mode", "cur", "profile"):
            item = value.get(key)
            if item not in (None, ""):
                preferred_parts.append(str(item))
        if preferred_parts:
            return " / ".join(preferred_parts)

        compact = [f"{key}:{item}" for key, item in value.items() if item not in (None, "", {})]
        return trim_text(", ".join(compact), 40) if compact else "Unavailable"

    if isinstance(value, (list, tuple, set)):
        text = ", ".join(str(item) for item in value if item is not None)
        return trim_text(text, 40) if text else "Unavailable"

    return str(value)


def stringify_fan(value: Any) -> str:
    if isinstance(value, dict):
        fan_name, fan_payload = next(iter(value.items()), (None, None))
        if isinstance(fan_payload, dict):
            rpm = first_list_item(fan_payload.get("rpm"))
            profile = fan_payload.get("profile")
            speed = first_list_item(fan_payload.get("speed"))
            parts = []
            if rpm is not None:
                parts.append(f"{int(float(rpm))} RPM")
            if speed is not None:
                parts.append(f"{float(speed):.0f}%")
            if profile:
                parts.append(str(profile))
            if fan_name and parts:
                return f"{fan_name}: {' / '.join(parts)}"
    return stringify_value(value)


def stringify_nvpmodel(value: Any) -> str:
    if isinstance(value, dict):
        return first_non_empty(
            stringify_value(value.get("name")),
            stringify_value(value.get("model")),
            stringify_value(value.get("id")),
        )
    return stringify_value(value)


def stringify_engines(value: Any) -> str:
    if isinstance(value, dict):
        active = []
        for engine_name, payload in value.items():
            text = stringify_value(payload)
            if "online:true" in text.lower() or "on" == text.lower():
                active.append(str(engine_name))
        if active:
            return ", ".join(active)
        return "Idle"
    return stringify_value(value)


def trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 1]}…"


def first_non_empty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}, ()):
            return value
    return None


def parse_numeric(value: Any) -> float | None:
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    match = re.search(r"-?\d+(?:\.\d+)?", str(value))
    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def format_bytes(value: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    size = float(value)
    unit_index = 0
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024.0
        unit_index += 1
    return f"{size:.1f} {units[unit_index]}"


def first_list_item(value: Any) -> Any:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return value


def extract_board_model(board: dict) -> str:
    info = normalize_keys(flatten_text_fields(board))
    return first_non_empty(info.get("hardware.model"), info.get("hardware.module")) or "Unavailable"


def extract_software_stack(board: dict) -> str:
    info = normalize_keys(flatten_text_fields(board))
    parts = [value for value in [info.get("hardware.jetpack"), info.get("hardware.l4t")] if value]
    return " / ".join(parts) if parts else "Unavailable"
