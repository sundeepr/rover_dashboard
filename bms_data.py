from __future__ import annotations

from datetime import datetime
import math
import os
from typing import Any


_POSTED_SNAPSHOT: dict[str, Any] | None = None
_MQTT_VALUES: dict[str, Any] = {}
_MQTT_STARTED = False

BMS_TOPIC_PREFIX = os.environ.get("BMS_MQTT_TOPIC_PREFIX", "jk-bms").strip("/")


def get_battery_snapshot() -> dict[str, Any]:
    source = os.environ.get("BMS_SOURCE", "mock").strip().lower()

    if source == "post" and _POSTED_SNAPSHOT:
        return normalize_bms_payload(_POSTED_SNAPSHOT, "simulator")

    if source == "mqtt":
        start_mqtt_client()
        if _MQTT_VALUES:
            return normalize_bms_payload(_MQTT_VALUES, "esphome-mqtt")
        return unavailable_bms("Waiting for ESPHome MQTT topics")

    if source == "disabled":
        return unavailable_bms("Battery telemetry disabled")

    return build_mock_bms()


def update_battery_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    global _POSTED_SNAPSHOT

    _POSTED_SNAPSHOT = payload
    return normalize_bms_payload(payload, "simulator")


def build_mock_bms() -> dict[str, Any]:
    now = datetime.now()
    wave = math.sin(now.timestamp() / 22.0)
    capacity = 82.0 + wave * 1.4
    current = -7.5 + wave * 1.8
    cell_base = 3.292 + wave * 0.006
    cells = [
        round(cell_base + math.sin((now.timestamp() / 18.0) + index) * 0.008, 3)
        for index in range(16)
    ]
    total_voltage = sum(cells)

    return build_bms_payload(
        source="mock",
        available=True,
        total_voltage=total_voltage,
        current=current,
        capacity_remaining=capacity,
        cells=cells,
        power_tube_temperature=31.0 + wave * 1.8,
        temperature_sensor_1=27.0 + wave,
        temperature_sensor_2=27.8 - wave,
        errors="None",
        operation_mode="Discharging",
        balancing=True,
        charging=False,
        discharging=True,
        device_type="JK-BMS mock",
        software_version="ESPHome simulator",
    )


def normalize_bms_payload(payload: dict[str, Any], source: str) -> dict[str, Any]:
    normalized = normalize_keys(payload)

    cells = extract_cells(normalized)
    total_voltage = first_number(
        normalized,
        "total_voltage",
        "total voltage",
        "jk-bms total voltage",
        "voltage",
    )
    current = first_number(normalized, "current", "jk-bms current")
    capacity_remaining = first_number(
        normalized,
        "capacity_remaining",
        "capacity remaining",
        "jk-bms capacity remaining",
        "soc",
        "state_of_charge",
    )

    return build_bms_payload(
        source=source,
        available=True,
        total_voltage=total_voltage,
        current=current,
        capacity_remaining=capacity_remaining,
        cells=cells,
        power_tube_temperature=first_number(
            normalized,
            "power_tube_temperature",
            "power tube temperature",
            "jk-bms power tube temperature",
        ),
        temperature_sensor_1=first_number(
            normalized,
            "temperature_sensor_1",
            "temperature sensor 1",
            "jk-bms temperature sensor 1",
        ),
        temperature_sensor_2=first_number(
            normalized,
            "temperature_sensor_2",
            "temperature sensor 2",
            "jk-bms temperature sensor 2",
        ),
        errors=first_text(normalized, "errors", "jk-bms errors") or "None",
        operation_mode=first_text(normalized, "operation_mode", "operation mode", "jk-bms operation mode"),
        balancing=first_bool(normalized, "balancing", "jk-bms balancing"),
        charging=first_bool(normalized, "charging", "jk-bms charging"),
        discharging=first_bool(normalized, "discharging", "jk-bms discharging"),
        device_type=first_text(normalized, "device_type", "device type", "jk-bms device type"),
        software_version=first_text(normalized, "software_version", "software version", "jk-bms software version"),
    )


def build_bms_payload(
    *,
    source: str,
    available: bool,
    total_voltage: float | None,
    current: float | None,
    capacity_remaining: float | None,
    cells: list[float],
    power_tube_temperature: float | None,
    temperature_sensor_1: float | None,
    temperature_sensor_2: float | None,
    errors: str,
    operation_mode: str | None,
    balancing: bool | None,
    charging: bool | None,
    discharging: bool | None,
    device_type: str | None,
    software_version: str | None,
) -> dict[str, Any]:
    min_cell = min(cells) if cells else None
    max_cell = max(cells) if cells else None
    delta_cell = max_cell - min_cell if min_cell is not None and max_cell is not None else None
    power = total_voltage * current if total_voltage is not None and current is not None else None
    status = classify_bms_status(capacity_remaining, errors, delta_cell)

    return {
        "available": available,
        "source": source,
        "updatedAt": datetime.now().strftime("%H:%M:%S"),
        "status": status,
        "state": operation_mode or infer_operation_mode(current, charging, discharging),
        "summary": {
            "capacityRemaining": format_percent(capacity_remaining),
            "totalVoltage": format_voltage(total_voltage),
            "current": format_current(current),
            "power": format_power(power),
            "cellDelta": format_voltage(delta_cell, precision=3),
        },
        "temperatures": [
            {"name": "Power Tube", "value": format_temperature(power_tube_temperature)},
            {"name": "Sensor 1", "value": format_temperature(temperature_sensor_1)},
            {"name": "Sensor 2", "value": format_temperature(temperature_sensor_2)},
        ],
        "cells": [
            {"index": index + 1, "voltage": format_voltage(value, precision=3), "rawVoltage": value}
            for index, value in enumerate(cells)
        ],
        "controls": {
            "balancing": format_switch(balancing),
            "charging": format_switch(charging),
            "discharging": format_switch(discharging),
        },
        "details": [
            {"name": "Errors", "value": errors or "None"},
            {"name": "Device", "value": device_type or "Unavailable"},
            {"name": "Software", "value": software_version or "Unavailable"},
            {"name": "Cells", "value": str(len(cells)) if cells else "Unavailable"},
        ],
    }


def unavailable_bms(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "source": os.environ.get("BMS_SOURCE", "mock"),
        "updatedAt": datetime.now().strftime("%H:%M:%S"),
        "status": "Unavailable",
        "state": reason,
        "summary": {
            "capacityRemaining": "Unavailable",
            "totalVoltage": "Unavailable",
            "current": "Unavailable",
            "power": "Unavailable",
            "cellDelta": "Unavailable",
        },
        "temperatures": [],
        "cells": [],
        "controls": {
            "balancing": "Unavailable",
            "charging": "Unavailable",
            "discharging": "Unavailable",
        },
        "details": [{"name": "Reason", "value": reason}],
    }


def start_mqtt_client() -> None:
    global _MQTT_STARTED

    if _MQTT_STARTED:
        return
    _MQTT_STARTED = True

    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return

    host = os.environ.get("BMS_MQTT_HOST", "127.0.0.1")
    port = int(os.environ.get("BMS_MQTT_PORT", "1883"))
    username = os.environ.get("BMS_MQTT_USERNAME")
    password = os.environ.get("BMS_MQTT_PASSWORD")

    def on_connect(client: Any, userdata: Any, flags: Any, reason_code: Any, properties: Any = None) -> None:
        client.subscribe(f"{BMS_TOPIC_PREFIX}/#")

    def on_message(client: Any, userdata: Any, message: Any) -> None:
        topic = message.topic.removeprefix(f"{BMS_TOPIC_PREFIX}/")
        payload = message.payload.decode("utf-8", errors="replace")
        parts = [part for part in topic.split("/") if part]
        entity = parts[-2] if len(parts) >= 2 and parts[-1] == "state" else parts[-1]
        key = clean_esphome_entity_key(entity)
        _MQTT_VALUES[key] = payload
        _MQTT_VALUES[topic.replace("/", " ")] = payload

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    if username:
        client.username_pw_set(username, password)
    client.on_connect = on_connect
    client.on_message = on_message

    try:
        client.connect_async(host, port, keepalive=30)
        client.loop_start()
    except Exception:
        _MQTT_STARTED = False


def extract_cells(payload: dict[str, Any]) -> list[float]:
    cells: list[float] = []
    raw_cells = payload.get("cells") or payload.get("cell_voltages") or payload.get("cell voltages")

    if isinstance(raw_cells, list):
        cells.extend(value for value in (to_number(item) for item in raw_cells) if value is not None)

    for index in range(1, 33):
        value = first_number(
            payload,
            f"cell_voltage_{index}",
            f"cell voltage {index}",
            f"jk-bms cell voltage {index}",
        )
        if value is not None:
            cells.append(value)

    return cells


def normalize_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {normalize_key(key): normalize_keys(item) for key, item in value.items()}
    if isinstance(value, list):
        return [normalize_keys(item) for item in value]
    return value


def normalize_key(value: Any) -> str:
    return str(value).strip().lower().replace("-", "_")


def clean_esphome_entity_key(value: Any) -> str:
    key = normalize_key(value).replace("rover_jk_bms_", "").replace("jk_bms_", "")
    return key.replace("_", " ")


def first_number(payload: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = to_number(payload.get(normalize_key(key)))
        if value is not None:
            return value
    return None


def first_text(payload: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(normalize_key(key))
        if value not in (None, ""):
            return str(value)
    return None


def first_bool(payload: dict[str, Any], *keys: str) -> bool | None:
    for key in keys:
        value = payload.get(normalize_key(key))
        if value is None:
            continue
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"on", "true", "1", "yes", "enabled"}:
            return True
        if text in {"off", "false", "0", "no", "disabled"}:
            return False
    return None


def to_number(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    number = ""
    for char in text:
        if char.isdigit() or char in ".-":
            number += char
        elif number:
            break
    try:
        return float(number)
    except ValueError:
        return None


def classify_bms_status(capacity: float | None, errors: str, delta_cell: float | None) -> str:
    if errors and errors.lower() not in {"none", "ok", "unavailable"}:
        return "Fault"
    if capacity is not None and capacity < 20:
        return "Low"
    if delta_cell is not None and delta_cell > 0.08:
        return "Imbalance"
    return "Healthy"


def infer_operation_mode(current: float | None, charging: bool | None, discharging: bool | None) -> str:
    if charging:
        return "Charging"
    if discharging:
        return "Discharging"
    if current is not None:
        if current > 0.2:
            return "Charging"
        if current < -0.2:
            return "Discharging"
    return "Idle"


def format_percent(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{max(0.0, min(value, 100.0)):.1f}%"


def format_voltage(value: float | None, precision: int = 2) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.{precision}f} V"


def format_current(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.2f} A"


def format_power(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.0f} W"


def format_temperature(value: float | None) -> str:
    if value is None:
        return "Unavailable"
    return f"{value:.1f} C"


def format_switch(value: bool | None) -> str:
    if value is None:
        return "Unavailable"
    return "On" if value else "Off"
