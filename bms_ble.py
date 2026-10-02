"""Read-only JK02 BLE telemetry for the Jetson (no ESPHome runtime required).

Protocol reference: syssi/esphome-jk-bms, components/jk_bms_ble/jk_bms_ble.cpp.
Python adaptation of frame layout/commands; see third_party/JK-BMS-LICENSE.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
import os
import re
import threading
import time

HEADER = bytes.fromhex('55 aa eb 90')
CHAR_UUID = '0000ffe1-0000-1000-8000-00805f9b34fb'
PROTOCOLS = {'JK02_24S', 'JK02_32S'}


def read_command(command: int) -> bytes:
    if command not in (0x96, 0x97):
        raise ValueError('Only telemetry and device-info requests are supported')
    packet = bytearray.fromhex('aa 55 90 eb') + bytearray([command]) + bytearray(14)
    return bytes(packet + bytes([sum(packet) & 255]))


class FrameBuffer:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, chunk):
        self.buffer.extend(chunk)
        frames = []
        while True:
            start = self.buffer.find(HEADER)
            if start < 0:
                self.buffer[:] = self.buffer[-3:]
                break
            del self.buffer[:start]
            if len(self.buffer) < 300:
                break
            if sum(self.buffer[:299]) & 255 != self.buffer[299]:
                del self.buffer[0]
                continue
            frames.append(bytes(self.buffer[:300]))
            del self.buffer[:300]
        return frames


def decode_status(frame: bytes, protocol: str) -> dict | None:
    if protocol not in PROTOCOLS:
        raise ValueError('Set BMS_BLE_PROTOCOL to JK02_24S or JK02_32S; JK04 is not supported')
    if len(frame) < 300 or frame[:4] != HEADER or sum(frame[:299]) & 255 != frame[299]:
        raise ValueError('Invalid JK-BMS frame')
    if frame[4] != 2:
        return None
    shift = 32 if protocol == 'JK02_32S' else 0

    def number(offset, size=2, signed=False):
        return int.from_bytes(frame[offset:offset + size], 'little', signed=signed)

    cells = [number(6 + i * 2) / 1000 for i in range(32 if shift else 24)]
    # Preserve cell numbering; remove unused trailing channels only.
    while cells and cells[-1] == 0:
        cells.pop()
    voltage = number(118 + shift, 4) / 1000
    soc = frame[141 + shift]
    if not cells or voltage <= 0 or soc > 100:
        raise ValueError('Invalid battery values; verify BMS_BLE_PROTOCOL')
    errors = number(134 + shift, 4) if shift else number(136)
    current = number(126 + shift, 4, True) / 1000
    return {
        'total_voltage': voltage, 'current': current, 'capacity_remaining': soc,
        'cells': cells,
        'power_tube_temperature': number(112 + shift if shift else 134, signed=True) / 10,
        'temperature_sensor_1': number(130 + shift, signed=True) / 10,
        'temperature_sensor_2': number(132 + shift, signed=True) / 10,
        'errors': f'BMS fault 0x{errors:08X}' if errors else 'None',
        'balancing': bool(frame[140 + shift]),
        'charging': bool(frame[166 + shift]), 'discharging': bool(frame[167 + shift]),
        # MOS enable flags do not describe actual current flow.
        'operation_mode': 'Charging' if current > 0.2 else 'Discharging' if current < -0.2 else 'Idle',
        'device_type': f'JK-BMS {protocol}',
    }


class BluetoothBattery:
    def __init__(self):
        self.lock = threading.Lock()
        self.thread = None
        self.payload = None
        self.received = 0.0
        self.updated_at = None
        self.reason = 'Connecting to JK-BMS Bluetooth'

    def snapshot(self):
        address = os.environ.get('BMS_BLE_ADDRESS', '').strip()
        protocol = os.environ.get('BMS_BLE_PROTOCOL', '').strip().upper()
        if not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', address):
            return None, None, 'Set BMS_BLE_ADDRESS to the BMS Bluetooth MAC address'
        if protocol not in PROTOCOLS:
            return None, None, 'Set BMS_BLE_PROTOCOL to JK02_24S or JK02_32S (JK04 unsupported)'
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self.run, args=(address, protocol), daemon=True)
                self.thread.start()
            if self.payload is not None and time.monotonic() - self.received <= 20:
                return dict(self.payload), self.updated_at, None
            return None, self.updated_at, self.reason if self.payload is None else 'Bluetooth battery data is stale'

    def unavailable(self, reason):
        with self.lock:
            self.payload = None
            self.reason = reason

    def run(self, address, protocol):
        asyncio.run(self.listen(address, protocol))

    async def listen(self, address, protocol):
        try:
            from bleak import BleakClient, BleakScanner
        except ImportError:
            self.unavailable('Install bleak==0.22.3 in the backend Python environment')
            return
        while True:
            try:
                device = await BleakScanner.find_device_by_address(address, timeout=10)
                if device is None:
                    raise RuntimeError('BMS not found; check power, range and Bluetooth adapter')
                async with BleakClient(device, timeout=15, disconnected_callback=lambda _: self.unavailable('Bluetooth disconnected; reconnecting')) as client:
                    chars = [c for service in client.services for c in service.characteristics if c.uuid.lower() == CHAR_UUID]
                    notify = next((c for c in chars if 'notify' in c.properties), None)
                    writer = next((c for c in chars if 'write-without-response' in c.properties), None)
                    if notify is None or writer is None:
                        raise RuntimeError('JK-BMS FFE1 notification/write characteristics not found')
                    buffer = FrameBuffer()
                    last_valid = time.monotonic()

                    def receive(_, chunk):
                        nonlocal last_valid
                        for frame in buffer.feed(chunk):
                            try:
                                payload = decode_status(frame, protocol)
                            except ValueError as exc:
                                self.unavailable(str(exc))
                                continue
                            if payload is not None:
                                last_valid = time.monotonic()
                                with self.lock:
                                    self.payload = payload
                                    self.received = last_valid
                                    self.updated_at = datetime.now().isoformat(timespec='seconds')
                                    self.reason = ''

                    await client.start_notify(notify, receive)
                    await client.write_gatt_char(writer, read_command(0x97), response=False)
                    await asyncio.sleep(1)
                    await client.write_gatt_char(writer, read_command(0x96), response=False)
                    while client.is_connected:
                        await asyncio.sleep(5)
                        if time.monotonic() - last_valid > 20:
                            raise RuntimeError('No valid battery frames for 20 seconds; reconnecting')
                self.unavailable('Bluetooth disconnected; reconnecting')
            except Exception as exc:
                self.unavailable(f'Bluetooth: {exc}')
            await asyncio.sleep(5)


battery_reader = BluetoothBattery()
