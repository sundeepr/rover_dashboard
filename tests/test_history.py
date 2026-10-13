import copy
import threading
import unittest
from unittest.mock import Mock

from telemetry_history import TelemetryHistory


def sample():
    return {'battery': {'available': True, 'source': 'sensorhub',
                        'summary': {'capacityRemaining': '74%', 'current': '-0.85 A', 'power': '-22 W'}},
            'status': {'source': 'sensorhub', 'cpuUsage': '20%'},
            'jetson': {'available': True, 'details': [{'name': 'RAM', 'value': '25%'}]}}


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000
        self.data = sample()
        self.source = Mock()
        self.source.snapshot.side_effect = lambda: copy.deepcopy(self.data)
        self.history = TelemetryHistory(self.source, clock=lambda: self.now, wall=lambda: 5000)

    def test_window_and_reconnecting_clients(self):
        for _ in range(200):
            self.history.collect()
            self.now += 2
        first = self.history.snapshot()['history']
        second = self.history.snapshot()['history']
        self.assertEqual(first, second)
        self.assertEqual(len(first['samples']), 150)
        self.assertEqual(first['samples'][0]['time'], first['now'] - 300000)
        self.assertEqual(first['samples'][-1]['battery'], {'Capacity': 74, 'Current': -.85, 'Power': -22})
        self.assertEqual(first['samples'][-1]['status']['cpuUsage'], 20)
        self.assertEqual(first['samples'][-1]['details']['RAM'], 25)
        self.now += 301
        self.assertEqual(self.history.snapshot()['history']['samples'], [])

    def test_unavailable_and_recovery_leave_gap(self):
        self.history.collect()
        self.now += 2
        self.data['battery']['available'] = False
        self.data['jetson']['available'] = False
        self.data['status']['source'] = 'unavailable'
        self.history.collect()
        gap = self.history.snapshot()['history']['samples'][-1]
        self.assertTrue(all(v is None for v in gap['battery'].values()))
        self.assertTrue(all(v is None for v in gap['status'].values()))
        self.assertEqual(gap['details'], {})
        self.now += 2
        self.data = sample()
        self.history.collect()
        self.assertEqual(len(self.history.snapshot()['history']['samples']), 3)

    def test_collector_runs_without_clients(self):
        sampled = threading.Event()
        self.source.snapshot.side_effect = lambda: (sampled.set() or sample())
        self.history.start()
        try:
            self.assertTrue(sampled.wait(1))
        finally:
            self.history.stopped.set()
            self.history.thread.join(1)
        self.assertEqual(len(self.history.points), 1)

    def test_failed_sample_is_gap_and_recovers(self):
        self.source.snapshot.side_effect = RuntimeError('offline')
        with self.assertLogs(level='ERROR'):
            self.history.collect()
        self.source.snapshot.side_effect = lambda: sample()
        self.now += 2
        self.history.collect()
        self.assertIsNone(self.history.points[0][1]['battery']['Current'])
        self.assertEqual(self.history.points[1][1]['battery']['Current'], -.85)

    def test_api_and_each_websocket_receive_existing_window(self):
        import dashboard_app
        from unittest.mock import patch
        self.history.collect()
        with patch.object(dashboard_app, 'telemetry', self.history):
            client = dashboard_app.app.test_client()
            payload = client.get('/api/telemetry').get_json()
            for _ in range(2):
                socket = Mock(connected=True)
                socket.receive.side_effect = lambda **kw: setattr(socket, 'connected', False)
                with dashboard_app.app.test_request_context('/ws'):
                    dashboard_app.live(socket)
                import json
                self.assertEqual(json.loads(socket.send.call_args.args[0])['history'], payload['history'])
        self.assertEqual(len(self.history.points), 1)

    def test_capacity_retains_48_hours_independently(self):
        for _ in range(2900):
            self.history.collect()
            self.now += 60
        history = self.history.snapshot()['history']
        capacity = history['capacity']
        self.assertEqual(capacity['windowMs'], 48 * 60 * 60 * 1000)
        self.assertEqual(len(capacity['samples']), 2880)
        self.assertEqual(capacity['samples'][0]['time'], history['now'] - capacity['windowMs'])
        self.assertEqual(len(history['samples']), 5)
        self.assertEqual(self.history.snapshot()['history'], history)
        self.now += self.history.CAPACITY_WINDOW + 1
        self.assertEqual(self.history.snapshot()['history']['capacity']['samples'], [])

    def test_capacity_minute_latest_and_gap(self):
        self.now = 1200
        self.history.collect()
        self.now += 2
        self.data['battery']['summary']['capacityRemaining'] = '73%'
        self.history.collect()
        points = self.history.snapshot()['history']['capacity']['samples']
        self.assertEqual(len(points), 1)
        self.assertEqual(points[0]['value'], 73)
        self.now += 2
        self.data['battery']['available'] = False
        self.history.collect()
        self.now += 2
        self.data['battery']['available'] = True
        self.history.collect()
        self.assertIsNone(self.history.snapshot()['history']['capacity']['samples'][0]['value'])
        self.now = 1260
        self.history.collect()
        points = self.history.snapshot()['history']['capacity']['samples']
        self.assertEqual([p['value'] for p in points], [None, 73])
