import importlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sensorhub_data import SensorHubTelemetry


def message(data):
    return SimpleNamespace(data=data, t_wall_ns=1790000000000000000)


class Hub:
    def __init__(self):
        self.cache = {}
        self.connected = True
    def is_connected(self): return self.connected
    def snapshot(self): return self.cache
    def put(self, instance, stream, data, age=0, state='running', heartbeat_age=0):
        self.cache[f'hub/sensors/{instance}/{stream}'] = (message(data), age)
        self.cache[f'hub/meta/drivers/{instance}/status'] = (message({'state':state}), heartbeat_age)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = SensorHubTelemetry()
        self.adapter.start = lambda: None
        self.adapter.hub = Hub()

    def test_disconnected_never_uses_mock_or_hardware(self):
        self.adapter.hub.connected = False
        with patch('bms_data.get_battery_snapshot', side_effect=AssertionError('hardware called')):
            data = self.adapter.snapshot()
        self.assertFalse(data['battery']['available'])
        self.assertFalse(data['gps']['available'])
        self.assertEqual(data['status']['cpuUsage'], 'Unavailable')
        self.assertEqual(data['sources']['odometry'], 'unavailable')

    def test_battery_mapping_and_warning(self):
        self.adapter.hub.put('battery', 'telemetry', {'soc_pct':74,'voltage_v':25.43,'current_a':-.85,
            'cells_v':[3.628,3.635], 'temps_c':{}, 'error_mask':0x80000,
            'alerts':[{'bit':19,'message':'Change BMS password','severity':'warning'}]})
        result = self.adapter.snapshot()['battery']
        self.assertTrue(result['available'])
        self.assertEqual(result['summary']['current'], '-0.85 A')
        self.assertEqual(result['status'], 'Warning')
        self.assertEqual(result['errorMask'], '0x00080000')

    def test_stale_and_degraded(self):
        for age, state, heartbeat_age in [(21,'running',0),(0,'degraded',0),(0,'running',6)]:
            self.adapter.hub.put('battery','telemetry',{},age,state,heartbeat_age)
            self.assertFalse(self.adapter.snapshot()['battery']['available'])

    def test_gps_no_fix_and_fresh_position(self):
        self.adapter.hub.put('gps','fix',{'fix':False,'latitude':None,'longitude':None})
        self.assertFalse(self.adapter.snapshot()['gps']['available'])
        self.adapter.hub.put('gps','fix',{'fix':True,'latitude':12.9,'longitude':77.6,'altitude_m':920})
        self.assertEqual(self.adapter.snapshot()['gps']['altitude'], 920)

    def test_jetson_mapping_nulls(self):
        self.adapter.hub.put('jetson','stats',{'cpu':{'cores':[{'usage_pct':10},{'usage_pct':30}]},
             'memory':{'ram_used_mb':100,'ram_total_mb':400},'temps_c':{'cpu':None},'gpu':{'usage_pct':12}})
        status=self.adapter.snapshot()['status']
        self.assertEqual(status['cpuUsage'],'20.0%')
        self.assertEqual(status['memoryUsage'],'25.0%')
        self.assertEqual(status['cpuTemp'],'Unavailable')


class RoutingTests(unittest.TestCase):
    def setUp(self):
        import dashboard_app
        self.module=dashboard_app
        self.client=dashboard_app.app.test_client()
    def test_built_assets_and_nested_refresh(self):
        for path in ['/', '/overview', '/overview/battery']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200)
            self.assertIn(b'<base href="/telemetry/"',response.data)
            response.close()
        with self.client.get('/app.js') as response:
            self.assertIn(b'connectLiveUpdates', response.data)
        with self.client.get('/styles.css') as response:
            self.assertEqual(response.status_code,200)
    def test_missing_assets_and_reserved_routes_not_html(self):
        for path in ['/missing.js','/api/missing','/api','/ws/missing']:
            self.assertEqual(self.client.get(path).status_code,404)
    def test_api_namespace_and_no_legacy_hardware_routes(self):
        self.assertEqual(self.client.get('/api/health').json['service'],'rover-dashboard')
        self.assertEqual(self.client.get('/api/bms/discover').status_code,404)
        self.assertEqual(self.client.post('/api/battery/simulate',json={}).status_code,405)
        with patch.object(self.module.telemetry,'snapshot',return_value={'battery':{'available':False}}):
            self.assertFalse(self.client.get('/api/battery').json['available'])
