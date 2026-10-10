import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('register_console', Path(__file__).resolve().parents[1] / 'deploy/register_console.py')
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)
BASE = '''{
    admin off
}
:80 {
    basicauth {
        admin {$CONSOLE_PASSWORD_HASH}
    }
    handle /api/* {
        reverse_proxy 127.0.0.1:8765
    }
    handle {
        root * /opt/rover-console/frontend/dist
        try_files {path} /index.html
        file_server
    }
}
'''


class InstallerTests(unittest.TestCase):
    def test_route_order_preserves_auth_and_console_api(self):
        result = registration.configure(BASE)
        self.assertIn('admin {$CONSOLE_PASSWORD_HASH}', result)
        self.assertIn('reverse_proxy 127.0.0.1:8765', result)
        self.assertLess(result.index('handle_path /telemetry/*'), result.index('    handle {'))
        self.assertIn('redir /telemetry /telemetry/ 308', result)

    def test_repeated_install_does_not_duplicate_route(self):
        once = registration.configure(BASE)
        self.assertEqual(registration.configure(once), once)
        self.assertEqual(once.count('handle_path /telemetry/*'), 1)

    def test_custom_route_is_not_overwritten(self):
        custom = BASE.replace('    handle {', '    handle_path /telemetry/* {\n        reverse_proxy localhost:8888\n    }\n    handle {')
        with self.assertRaises(ValueError):
            registration.configure(custom)

    def test_unknown_console_layout_fails(self):
        with self.assertRaises(ValueError):
            registration.configure(':80 { reverse_proxy localhost:8765 }')

    def test_prepare_does_not_modify_live_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'Caddyfile'; config.write_text(BASE)
            manifest = root / 'telemetry.json'
            registration.prepare(config, manifest, root / 'staged')
            self.assertEqual(config.read_text(), BASE)
            self.assertFalse(manifest.exists())
            self.assertTrue((root / 'staged/telemetry.json').exists())

    def test_conflicting_registration_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / 'Caddyfile'; config.write_text(BASE)
            manifest = root / 'telemetry.json'; manifest.write_text('{"id":"different"}')
            with self.assertRaises(ValueError):
                registration.prepare(config, manifest, root / 'staged')
            self.assertEqual(config.read_text(), BASE)
