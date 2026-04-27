from __future__ import annotations

from datetime import datetime
import asyncio
import platform
import re
import socket
import subprocess
from typing import Any


ESPHOME_SERVICE_TYPES = ("_esphomelib._tcp.local.", "_http._tcp.local.")
JK_BMS_NAME_HINTS = ("jk", "bms", "xiaoxiang", "jkbms")


def discover_bms_devices(timeout: float = 4.0) -> dict[str, Any]:
    return {
        "updatedAt": datetime.now().strftime("%H:%M:%S"),
        "network": discover_network_devices(timeout),
        "bluetooth": discover_bluetooth_devices(timeout),
    }


def discover_network_devices(timeout: float) -> dict[str, Any]:
    devices = []
    notes = []

    mdns_result = discover_mdns_devices(timeout)
    devices.extend(mdns_result["devices"])
    notes.extend(mdns_result["notes"])

    arp_result = discover_arp_devices()
    devices.extend(arp_result["devices"])
    notes.extend(arp_result["notes"])

    return {
        "available": True,
        "devices": dedupe_devices(devices),
        "notes": notes,
    }


def discover_bluetooth_devices(timeout: float) -> dict[str, Any]:
    try:
        return asyncio.run(discover_ble_devices(timeout))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(discover_ble_devices(timeout))
        finally:
            loop.close()


async def discover_ble_devices(timeout: float) -> dict[str, Any]:
    try:
        from bleak import BleakScanner
    except ImportError:
        return {
            "available": False,
            "devices": [],
            "notes": ["Install bleak to scan nearby BLE JK-BMS devices."],
        }

    try:
        discovered = await BleakScanner.discover(timeout=timeout)
    except Exception as exc:
        return {
            "available": False,
            "devices": [],
            "notes": [f"Bluetooth scan failed: {exc}"],
        }

    devices = []
    for device in discovered:
        name = device.name or "Unknown BLE device"
        address = getattr(device, "address", "unknown")
        devices.append(
            {
                "kind": "ble",
                "name": name,
                "address": address,
                "likelyBms": looks_like_bms(name),
                "source": "bleak",
            }
        )

    return {
        "available": True,
        "devices": devices,
        "notes": [] if devices else ["No BLE advertisements found during the scan window."],
    }


def discover_mdns_devices(timeout: float) -> dict[str, Any]:
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return {
            "devices": [],
            "notes": ["Install zeroconf to discover ESPHome devices with mDNS."],
        }

    class Listener(ServiceListener):
        def __init__(self) -> None:
            self.devices: list[dict[str, Any]] = []

        def add_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
            info = zeroconf.get_service_info(service_type, name, timeout=int(timeout * 1000))
            if info is None:
                return

            addresses = [socket.inet_ntoa(address) for address in info.addresses if len(address) == 4]
            self.devices.append(
                {
                    "kind": "network",
                    "name": name.removesuffix(f".{service_type}"),
                    "address": addresses[0] if addresses else "unknown",
                    "port": info.port,
                    "service": service_type,
                    "likelyBms": looks_like_bms(name),
                    "source": "mdns",
                }
            )

        def update_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
            self.add_service(zeroconf, service_type, name)

        def remove_service(self, zeroconf: Zeroconf, service_type: str, name: str) -> None:
            return

    listener = Listener()
    zeroconf = Zeroconf()
    browsers = []
    try:
        browsers = [ServiceBrowser(zeroconf, service_type, listener) for service_type in ESPHOME_SERVICE_TYPES]
        asyncio.run(asyncio.sleep(timeout))
    except Exception as exc:
        return {
            "devices": [],
            "notes": [f"mDNS discovery failed: {exc}"],
        }
    finally:
        for browser in browsers:
            try:
                browser.cancel()
            except Exception:
                pass
        zeroconf.close()

    return {
        "devices": listener.devices,
        "notes": [] if listener.devices else ["No ESPHome mDNS services found during the scan window."],
    }


def discover_arp_devices() -> dict[str, Any]:
    if platform.system() == "Windows":
        command = ["arp", "-a"]
    else:
        command = ["arp", "-a"]

    try:
        completed = subprocess.run(command, capture_output=True, check=False, text=True, timeout=3)
    except Exception as exc:
        return {
            "devices": [],
            "notes": [f"ARP scan failed: {exc}"],
        }

    devices = []
    for line in completed.stdout.splitlines():
        match = re.search(r"\((?P<ip>\d+\.\d+\.\d+\.\d+)\)\s+at\s+(?P<mac>[0-9a-f:]+)", line, re.I)
        if not match:
            continue
        devices.append(
            {
                "kind": "network",
                "name": "LAN device",
                "address": match.group("ip"),
                "mac": match.group("mac"),
                "likelyBms": False,
                "source": "arp",
            }
        )

    return {
        "devices": devices,
        "notes": [] if devices else ["No devices found in the ARP cache."],
    }


def looks_like_bms(value: str) -> bool:
    normalized = value.lower()
    return any(hint in normalized for hint in JK_BMS_NAME_HINTS)


def dedupe_devices(devices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = []
    seen = set()

    for device in devices:
        key = (device.get("source"), device.get("address"), device.get("name"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(device)

    return deduped
