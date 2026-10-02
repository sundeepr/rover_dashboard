import os
import unittest
from unittest.mock import patch

from bms_ble import BluetoothBattery, FrameBuffer, HEADER, decode_status, read_command
from bms_data import get_battery_snapshot


def status_frame(protocol='JK02_32S'):
    data = bytearray(300)
    data[:4] = HEADER
    data[4] = 2
    shift = 32 if protocol == 'JK02_32S' else 0
    def put(offset, value, size=2, signed=False):
        data[offset:offset + size] = value.to_bytes(size, 'little', signed=signed)
    for i in range(16):
        put(6 + i * 2, 3300)
    put(118 + shift, 52800, 4)
    put(126 + shift, -7500, 4, True)
    put(112 + shift if shift else 134, 310)
    put(130 + shift, -50, signed=True)
    put(132 + shift, 280)
    data[141 + shift] = 82
    data[166 + shift] = 1
    data[167 + shift] = 1
    data[299] = sum(data[:299]) & 255
    return bytes(data)


class BatteryTests(unittest.TestCase):
    def test_password_warning_and_mixed_fault(self):
        for mask, expected in ((0x80000, 'Warning'), (0x80020, 'Fault'), (0, 'Healthy')):
            frame = bytearray(status_frame())
            frame[166:170] = mask.to_bytes(4, 'little')
            frame[299] = sum(frame[:299]) & 255
            payload = decode_status(frame, 'JK02_32S')
            with patch.dict(os.environ, {'BMS_SOURCE': 'ble'}), patch(
                'bms_ble.battery_reader.snapshot', return_value=(payload, 'now', None)
            ):
                result = get_battery_snapshot()
            self.assertEqual(result['status'], expected)
            self.assertEqual(result['errorMask'], f'0x{mask:08X}')
            if mask & 0x80000:
                self.assertIn('Change BMS password', [a['message'] for a in result['alerts']])

    def test_unknown_bits_remain_faults(self):
        from bms_ble import decode_alerts
        self.assertEqual(decode_alerts(1 << 31)[0]['severity'], 'fault')
        self.assertEqual(decode_alerts(1 << 4)[0]['severity'], 'info')

    def test_both_protocols_and_current_direction(self):
        for protocol in ('JK02_24S', 'JK02_32S'):
            result = decode_status(status_frame(protocol), protocol)
            self.assertEqual(result['total_voltage'], 52.8)
            self.assertEqual(result['current'], -7.5)
            self.assertEqual(result['capacity_remaining'], 82)
            self.assertEqual(result['temperature_sensor_1'], -5)
            self.assertEqual(result['power_tube_temperature'], 31)
            self.assertEqual(len(result['cells']), 16)
            self.assertEqual(result['operation_mode'], 'Discharging')

    def test_fragmentation_and_multiple_frames(self):
        frame = status_frame()
        for split in range(1, 300):
            buf = FrameBuffer()
            self.assertEqual(buf.feed(frame[:split]), [])
            self.assertEqual(buf.feed(frame[split:]), [frame])
        self.assertEqual(FrameBuffer().feed(frame + frame), [frame, frame])

    def test_corrupt_frame_recovers(self):
        frame = status_frame()
        bad = bytearray(frame)
        bad[7] ^= 1
        self.assertEqual(FrameBuffer().feed(b'noise' + bad + frame), [frame])
        with self.assertRaises(ValueError):
            decode_status(bad, 'JK02_32S')

    def test_invalid_soc_and_unsupported_protocol(self):
        bad = bytearray(status_frame())
        bad[173] = 255
        bad[299] = sum(bad[:299]) & 255
        with self.assertRaises(ValueError):
            decode_status(bad, 'JK02_32S')
        with self.assertRaises(ValueError):
            decode_status(status_frame(), 'JK04')

    def test_read_only_commands(self):
        self.assertEqual(read_command(0x96).hex(), 'aa5590eb96000000000000000000000000000010')
        with self.assertRaises(ValueError):
            read_command(1)

    def test_no_configuration_never_returns_mock(self):
        with patch.dict(os.environ, {'BMS_SOURCE': 'ble', 'BMS_BLE_ADDRESS': ''}):
            data = get_battery_snapshot()
        self.assertFalse(data['available'])
        self.assertEqual(data['source'], 'ble')
        self.assertIsNone(data['updatedAt'])

    def test_fresh_stale_and_disconnected(self):
        reader = BluetoothBattery()
        reader.thread = object()  # No hardware worker in this test.
        reader.payload = decode_status(status_frame(), 'JK02_32S')
        reader.received = 100
        reader.updated_at = 'received-time'
        config = {'BMS_BLE_ADDRESS': 'AA:BB:CC:DD:EE:FF', 'BMS_BLE_PROTOCOL': 'JK02_32S'}
        with patch.dict(os.environ, config), patch('bms_ble.time.monotonic', return_value=110):
            self.assertIsNotNone(reader.snapshot()[0])
        with patch.dict(os.environ, config), patch('bms_ble.time.monotonic', return_value=121):
            self.assertIsNone(reader.snapshot()[0])
            self.assertIn('stale', reader.snapshot()[2])
        reader.unavailable('Disconnected')
        with patch.dict(os.environ, config):
            self.assertEqual(reader.snapshot(), (None, 'received-time', 'Disconnected'))


if __name__ == '__main__':
    unittest.main()
