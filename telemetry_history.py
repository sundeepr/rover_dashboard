"""Shared telemetry history buffers; independent of HTTP/WebSocket clients."""
from collections import deque
import logging
import math
import re
import threading
import time


def numeric(value):
    match = re.match(r'^\s*([-+]?\d+(?:\.\d+)?)', str(value))
    result = float(match[1]) if match else None
    return result if result is not None and math.isfinite(result) else None


class TelemetryHistory:
    WINDOW = 300
    INTERVAL = 2
    CAPACITY_WINDOW = 48 * 60 * 60
    CAPACITY_INTERVAL = 60

    def __init__(self, source, clock=time.monotonic, wall=time.time):
        self.source, self.clock, self.wall = source, clock, wall
        self.points = deque(maxlen=151)
        self.capacity_points = deque(maxlen=self.CAPACITY_WINDOW // self.CAPACITY_INTERVAL + 1)
        self.lock = threading.Lock()
        self.stopped = threading.Event()
        self.thread = None

    def start(self):
        # Called once in the single Gunicorn worker (do not use --preload).
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, name='telemetry-history', daemon=True)
            self.thread.start()

    def run(self):
        while not self.stopped.is_set():
            self.collect()
            self.stopped.wait(self.INTERVAL)

    def collect(self):
        try:
            data = self.source.snapshot()
        except Exception:
            logging.exception('Telemetry sample unavailable')
            data = {}
        battery = data.get('battery', {})
        status = data.get('status', {})
        jetson = data.get('jetson', {})
        summary = battery.get('summary', {}) if battery.get('available') else {}
        point = {
            'battery': {key: numeric(summary.get(field)) for key, field in
                        [('Capacity', 'capacityRemaining'), ('Current', 'current'), ('Power', 'power')]},
            'status': {key: numeric(status.get(key)) if status.get('source') != 'unavailable' else None
                       for key in ('cpuUsage', 'memoryUsage', 'cpuTemp', 'gpuUsage')},
            'details': {item['name']: numeric(item.get('value')) for item in jetson.get('details', [])}
                       if jetson.get('available') else {},
            'batterySource': battery.get('source'), 'statusSource': status.get('source'),
        }
        capacity = point['battery']['Capacity']
        if capacity is not None and not 0 <= capacity <= 100:
            point['battery']['Capacity'] = None
        with self.lock:
            now = self.clock()
            self.points.append((now, point))
            # Keep the latest reading per minute. A missing reading anywhere in
            # that minute makes it a gap rather than concealing a brief outage.
            bucket = int(now // self.CAPACITY_INTERVAL)
            value = point['battery']['Capacity']
            source = point['batterySource']
            if self.capacity_points and self.capacity_points[-1][1]['bucket'] == bucket:
                _, previous = self.capacity_points.pop()
                if previous['value'] is None or previous['source'] != source:
                    value = None
            self.capacity_points.append((now, {'bucket': bucket, 'value': value, 'source': source}))
            self.prune(now)

    def prune(self, now):
        while self.points and self.points[0][0] < now - self.WINDOW:
            self.points.popleft()
        while self.capacity_points and self.capacity_points[0][0] < now - self.CAPACITY_WINDOW:
            self.capacity_points.popleft()

    def snapshot(self):
        data = self.source.snapshot()
        with self.lock:
            now, wall = self.clock(), self.wall() * 1000
            self.prune(now)
            data['history'] = {'now': wall, 'windowMs': self.WINDOW * 1000,
                               'samples': [dict(point, time=wall - (now - stamp) * 1000)
                                           for stamp, point in self.points],
                               'capacity': {'windowMs': self.CAPACITY_WINDOW * 1000,
                                            'samples': [{'time': wall - (now - stamp) * 1000,
                                                         'value': point['value'], 'source': point['source']}
                                                        for stamp, point in self.capacity_points]}}
        return data
