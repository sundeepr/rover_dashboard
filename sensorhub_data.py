"""Read-only SensorHub adapter. No hardware connections or command publishing."""
from __future__ import annotations

from datetime import datetime, timezone
import math
import os
import threading
import time

from bms_data import normalize_bms_payload, unavailable_bms


def number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def formatted(value, unit):
    value = number(value)
    return f'{value:.1f}{unit}' if value is not None else 'Unavailable'


def timestamp(message):
    return datetime.fromtimestamp(message.t_wall_ns / 1e9, timezone.utc).isoformat()


class SensorHubTelemetry:
    def __init__(self):
        self.hub = None
        self.reason = 'Connecting to SensorHub'
        self.lock = threading.Lock()
        self.thread = None
        self.instances = {key: os.environ.get(f'SENSORHUB_{key.upper()}_INSTANCE', key)
                          for key in ('battery', 'gps', 'jetson', 'imu')}

    def start(self):
        with self.lock:
            if self.thread is None:
                self.thread = threading.Thread(target=self.connect, daemon=True)
                self.thread.start()

    def connect(self):
        while self.hub is None:
            hub = None
            try:
                from vendor.sensorhub_client import Hub
                hub = Hub(connect_timeout=5)
                topics = []
                for key, stream in [('battery', 'telemetry'), ('gps', 'fix'), ('jetson', 'stats'), ('imu', 'data')]:
                    name = self.instances[key]
                    topics.extend([f'hub/sensors/{name}/{stream}', f'hub/meta/drivers/{name}/status'])
                hub.watch(*topics)
                self.hub = hub
            except Exception as exc:
                if hub:
                    hub.close()
                self.reason = f'SensorHub unavailable: {type(exc).__name__}'
                time.sleep(5)

    def read(self, cache, key, stream, max_age):
        name = self.instances[key]
        entry = cache.get(f'hub/sensors/{name}/{stream}')
        heartbeat = cache.get(f'hub/meta/drivers/{name}/status')
        if not heartbeat or heartbeat[1] > 5:
            return None, 'Missing or stale driver heartbeat'
        health = heartbeat[0].data
        if health.get('state') != 'running':
            return None, health.get('last_error') or f"Driver {health.get('state', 'unavailable')}"
        if not entry or entry[1] > max_age:
            return None, 'Missing or stale telemetry'
        return entry[0], None

    def snapshot(self):
        self.start()
        connected = self.hub is not None and self.hub.is_connected()
        cache = self.hub.snapshot() if connected else {}
        battery, battery_reason = self.read(cache, 'battery', 'telemetry', 20)
        gps, gps_reason = self.read(cache, 'gps', 'fix', 5)
        jetson, jetson_reason = self.read(cache, 'jetson', 'stats', 3)
        imu, imu_reason = self.read(cache, 'imu', 'data', 2)
        status = {key: 'Unavailable' for key in ('cpuUsage', 'memoryUsage', 'cpuTemp', 'gpuUsage')}
        status.update(source='unavailable', updatedAt=None)
        jetson_payload = {'available': False, 'source': 'unavailable', 'details': [], 'error': jetson_reason}
        if jetson:
            data = jetson.data
            usages = [number(c.get('usage_pct')) for c in data.get('cpu', {}).get('cores', [])]
            usages = [v for v in usages if v is not None]
            memory = data.get('memory', {})
            used, total = number(memory.get('ram_used_mb')), number(memory.get('ram_total_mb'))
            status.update(cpuUsage=formatted(sum(usages) / len(usages) if usages else None, '%'),
                          memoryUsage=formatted(used / total * 100 if used is not None and total and total > 0 else None, '%'),
                          cpuTemp=formatted(data.get('temps_c', {}).get('cpu'), ' C'),
                          gpuUsage=formatted(data.get('gpu', {}).get('usage_pct'), '%'),
                          updatedAt=timestamp(jetson), source='sensorhub')
            jetson_payload = {'available': True, 'source': 'sensorhub', 'details': [
                {'name': 'RAM', 'value': status['memoryUsage']},
                *[{'name': f'Temp {k}', 'value': formatted(v, ' C')} for k, v in data.get('temps_c', {}).items()],
                {'name': 'Power', 'value': formatted(data.get('power_mw', {}).get('total'), ' mW')}]}
        battery_payload = unavailable_bms(battery_reason or self.reason)
        battery_payload.update(source='sensorhub', updatedAt=None)
        if battery:
            data = battery.data
            alerts = data.get('alerts', [])
            temps = data.get('temps_c', {})
            battery_payload = normalize_bms_payload({
                'capacity_remaining': data.get('soc_pct'), 'total_voltage': data.get('voltage_v'),
                'current': data.get('current_a'), 'cells': data.get('cells_v'),
                'power_tube_temperature': temps.get('power_tube'), 'temperature_sensor_1': temps.get('sensor_1'),
                'temperature_sensor_2': temps.get('sensor_2'), 'balancing': data.get('balancing'),
                'charging': data.get('charge_mos_on'), 'discharging': data.get('discharge_mos_on'),
                'operation_mode': data.get('operation_mode'),
                'errors': '; '.join(a['message'] for a in alerts if a['severity'] == 'fault') or 'None',
                'device_type': f"JK-BMS {data.get('protocol', '')}"},
                'simulator' if data.get('protocol') == 'simulated' else 'sensorhub')
            battery_payload.update(alerts=alerts, errorMask=f"0x{data.get('error_mask', 0):08X}", updatedAt=timestamp(battery))
            if battery_payload['status'] == 'Healthy' and any(a['severity'] == 'warning' for a in alerts):
                battery_payload['status'] = 'Warning'
        gps_payload = {'available': False, 'source': 'unavailable', 'reason': gps_reason, 'latitude': None, 'longitude': None}
        if gps:
            data = gps.data
            gps_payload = dict(available=data.get('fix') is True, source='sensorhub',
                               latitude=data.get('latitude'), longitude=data.get('longitude'),
                               altitude=data.get('altitude_m'), satellites=data.get('satellites_used'),
                               fix=data.get('fix_type'), updatedAt=timestamp(gps), reason='Waiting for GPS fix')
        devices = []
        for key, message, reason in [('battery', battery, battery_reason), ('gps', gps, gps_reason),
                                     ('jetson', jetson, jetson_reason), ('imu', imu, imu_reason)]:
            devices.append({'name': self.instances[key], 'port': 'SensorHub',
                            'available': message is not None, 'status': 'online' if message else reason})
        return {'connection': {'available': connected, 'message': 'SensorHub connected' if connected else self.reason},
                'status': status, 'battery': battery_payload, 'gps': gps_payload, 'jetson': jetson_payload,
                'devices': devices, 'sources': {'devices': 'sensorhub' if any((battery, gps, jetson, imu)) else 'unavailable', 'odometry': 'unavailable'},
                'odometry': {'x': '--', 'y': '--', 'heading': '--',
                             'speed': '--', 'wheelTicks': '--', 'frame': '--'},
                'sensors': [], 'imu': {'available': imu is not None, 'data': imu.data if imu else None}}
