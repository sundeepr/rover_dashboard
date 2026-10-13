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
                          for key in ('battery', 'gps', 'jetson', 'imu', 'controller')}

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
                for key, stream in [('battery', 'telemetry'), ('gps', 'fix'), ('jetson', 'stats'), ('imu', 'data'), ('controller', 'telemetry')]:
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
        controller, controller_reason = self.read(cache, 'controller', 'telemetry', 2)
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
        gps_payload = {'available': False, 'receiving': gps is not None, 'source': 'unavailable',
                       'reason': gps_reason, 'latitude': None, 'longitude': None, 'updatedAt': None}
        if gps:
            data = gps.data
            lat, lon = number(data.get('latitude')), number(data.get('longitude'))
            coordinates_valid = lat is not None and lon is not None and abs(lat) <= 90 and abs(lon) <= 180
            available = data.get('fix') is True and coordinates_valid
            gps_payload.update(available=available, source='sensorhub',
                               latitude=lat if available else None, longitude=lon if available else None,
                               altitude=number(data.get('altitude_m')) if available else None,
                               speed=number(data.get('speed_mps')) if available else None,
                               course=number(data.get('course_deg')) if available else None,
                               satellites=number(data.get('satellites_used')),
                               satellitesInView=number(data.get('satellites_in_view')),
                               hdop=number(data.get('hdop')), fix=data.get('fix_type'),
                               fixQuality=number(data.get('fix_quality')), updatedAt=timestamp(gps),
                               reason=None if available else ('Invalid GPS coordinates' if data.get('fix') is True
                                                              else 'Waiting for GPS fix'))
        imu_payload = {'available': False, 'source': 'unavailable', 'reason': imu_reason,
                       'updatedAt': None, 'data': None}
        if imu:
            data = imu.data
            reading = {key: {axis: number((data.get(key) or {}).get(axis)) for axis in axes}
                       for key, axes in [('euler_deg', ('roll', 'pitch', 'yaw')),
                                         ('quaternion', ('w', 'x', 'y', 'z')),
                                         *[(key, ('x', 'y', 'z')) for key in
                                           ('accel_mps2', 'gyro_rads', 'mag_ut', 'linear_accel_mps2', 'gravity_mps2')]]}
            valid = any(value is not None for vector in reading.values() for value in vector.values())
            reading.update(heading_deg=number(data.get('heading_deg')), compass=data.get('compass'),
                           mag_accuracy=number(data.get('mag_accuracy')))
            valid = valid or reading['heading_deg'] is not None
            imu_payload.update(available=valid, source='sensorhub' if valid else 'unavailable',
                               reason=None if valid else 'No valid IMU readings', updatedAt=timestamp(imu), data=reading)
        controller_payload = {'available': False, 'source': 'unavailable', 'reason': controller_reason,
                              'updatedAt': None, 'data': None}
        if controller:
            data = controller.data
            reading = {key: data.get(key) if isinstance(data.get(key), bool) else None
                       for key in ('armed', 'autonomy', 'host_stale', 'wheel_base_matches')}
            reading.update({key: number(data.get(key)) for key in ('board_t_ms', 'wheel_base_mm', 'pump_cmd')})
            for key, fields in [('rc', ('throttle', 'steer')), ('host', ('throttle', 'radius_mm', 'aux')),
                                ('cmd', ('left', 'right'))]:
                values = data.get(key) if isinstance(data.get(key), dict) else {}
                reading[key] = {field: number(values.get(field)) for field in fields}
            reading['extra'] = data.get('extra') if isinstance(data.get('extra'), dict) else {}
            controller_payload.update(available=True, source='sensorhub', reason=None,
                                      updatedAt=timestamp(controller), data=reading)
        devices = []
        for key, message, reason in [('battery', battery, battery_reason), ('gps', gps, gps_reason),
                                     ('jetson', jetson, jetson_reason), ('imu', imu, imu_reason),
                                     ('controller', controller, controller_reason)]:
            devices.append({'name': self.instances[key], 'port': 'SensorHub',
                            'available': message is not None, 'status': 'online' if message else reason})
        return {'connection': {'available': connected, 'message': 'SensorHub connected' if connected else self.reason},
                'status': status, 'battery': battery_payload, 'gps': gps_payload, 'jetson': jetson_payload,
                'devices': devices, 'sources': {'devices': 'sensorhub' if any((battery, gps, jetson, imu, controller)) else 'unavailable', 'odometry': 'unavailable'},
                'odometry': {'x': '--', 'y': '--', 'heading': '--',
                             'speed': '--', 'wheelTicks': '--', 'frame': '--'},
                'sensors': [], 'imu': imu_payload, 'controller': controller_payload}
