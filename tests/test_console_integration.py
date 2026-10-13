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

    def test_gps_diagnostics_without_fix_and_invalid_coordinates(self):
        self.adapter.hub.put('gps', 'fix', {'fix': False, 'satellites_used': 0,
                                         'satellites_in_view': 8, 'fix_type': 'none'})
        gps = self.adapter.snapshot()['gps']
        self.assertTrue(gps['receiving'])
        self.assertFalse(gps['available'])
        self.assertEqual(gps['satellitesInView'], 8)
        self.assertEqual(gps['reason'], 'Waiting for GPS fix')
        for lat, lon in [(91, 77), (12, float('nan')), (True, 77)]:
            self.adapter.hub.put('gps', 'fix', {'fix': True, 'latitude': lat, 'longitude': lon})
            gps = self.adapter.snapshot()['gps']
            self.assertFalse(gps['available'])
            self.assertIsNone(gps['latitude'])

    def test_gps_speed_and_imu_orientation_units(self):
        self.adapter.hub.put('gps', 'fix', {'fix': True, 'latitude': 0, 'longitude': 0,
                                         'speed_mps': 1.2, 'course_deg': 45, 'hdop': .9})
        self.adapter.hub.put('imu', 'data', {'euler_deg': {'roll': -2, 'pitch': 3, 'yaw': 90},
            'heading_deg': 90, 'compass': 'E', 'accel_mps2': {'x': 0, 'y': 0, 'z': 9.81},
            'gyro_rads': {'x': 0, 'y': 0, 'z': .2}, 'mag_ut': None, 'mag_accuracy': 0})
        data = self.adapter.snapshot()
        self.assertTrue(data['gps']['available'])
        self.assertEqual(data['gps']['speed'], 1.2)
        self.assertEqual(data['gps']['course'], 45)
        imu = data['imu']
        self.assertTrue(imu['available'])
        self.assertEqual(imu['data']['euler_deg']['roll'], -2)
        self.assertEqual(imu['data']['accel_mps2']['z'], 9.81)
        self.assertEqual(imu['data']['gyro_rads']['z'], .2)
        self.assertEqual(imu['data']['mag_accuracy'], 0)
        self.assertIsNone(imu['data']['mag_ut']['x'])
        self.assertIsNotNone(imu['updatedAt'])

    def test_gps_imu_expire_and_custom_instance_names(self):
        self.adapter.instances.update(gps='position', imu='orientation')
        for key, name, stream, fields, max_age in [
                ('gps', 'position', 'fix', {'fix': True, 'latitude': 12, 'longitude': 77}, 5),
                ('imu', 'orientation', 'data', {'heading_deg': 0}, 2)]:
            self.adapter.hub.put(name, stream, fields)
            self.assertTrue(self.adapter.snapshot()[key]['available'])
            for age, heartbeat_age, state in [(max_age + .1, 0, 'running'), (0, 6, 'running'), (0, 0, 'degraded')]:
                self.adapter.hub.put(name, stream, fields, age=age, heartbeat_age=heartbeat_age, state=state)
                payload = self.adapter.snapshot()[key]
                self.assertFalse(payload['available'])
                self.assertIsNotNone(payload['reason'])

    def test_controller_sample_and_device(self):
        sample = {'board_t_ms': 1039228, 'armed': True, 'autonomy': False, 'host_stale': True,
                  'rc': {'throttle': 0, 'steer': 0},
                  'host': {'throttle': None, 'radius_mm': None, 'aux': None},
                  'wheel_base_mm': 650, 'cmd': {'left': 0, 'right': 0}, 'pump_cmd': 1,
                  'extra': {}, 'wheel_base_matches': True}
        self.adapter.hub.put('controller', 'telemetry', sample)
        result = self.adapter.snapshot()
        controller = result['controller']
        self.assertTrue(controller['available'])
        self.assertEqual(controller['data'], sample)
        self.assertIsNotNone(controller['updatedAt'])
        self.assertEqual(result['sources']['devices'], 'sensorhub')
        self.assertTrue(next(d for d in result['devices'] if d['name'] == 'controller')['available'])

    def test_controller_stale_degraded_and_recovery(self):
        for age, state, heartbeat_age in [(2.1, 'running', 0), (0, 'degraded', 0), (0, 'running', 6)]:
            self.adapter.hub.put('controller', 'telemetry', {'armed': True}, age, state, heartbeat_age)
            result = self.adapter.snapshot()['controller']
            self.assertFalse(result['available'])
            self.assertIsNone(result['data'])
            self.assertIsNotNone(result['reason'])
        self.adapter.hub.put('controller', 'telemetry', {'armed': False, 'host_stale': False})
        self.assertTrue(self.adapter.snapshot()['controller']['available'])
        self.adapter.hub.connected = False
        self.assertFalse(self.adapter.snapshot()['controller']['available'])

    def test_controller_custom_subscription_and_unknown_values(self):
        with patch.dict('os.environ', {'SENSORHUB_CONTROLLER_INSTANCE': 'stm32'}):
            adapter = SensorHubTelemetry()
        with patch('vendor.sensorhub_client.Hub') as constructor:
            adapter.connect()
            topics = constructor.return_value.watch.call_args.args
            self.assertIn('hub/sensors/stm32/telemetry', topics)
            self.assertIn('hub/meta/drivers/stm32/status', topics)
        adapter.start = lambda: None
        adapter.hub = Hub()
        adapter.hub.put('stm32', 'telemetry', {'armed': 'false', 'pump_cmd': float('nan'),
                                            'rc': None, 'cmd': {'left': -20, 'right': 20},
                                            'extra': {'temperature': 32}})
        data = adapter.snapshot()['controller']['data']
        self.assertIsNone(data['armed'])
        self.assertIsNone(data['pump_cmd'])
        self.assertIsNone(data['rc']['throttle'])
        self.assertEqual(data['cmd']['left'], -20)
        self.assertEqual(data['extra']['temperature'], 32)

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
