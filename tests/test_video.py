import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

from video import cameras, relay_config


class VideoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config = Path(self.directory.name) / 'cameras.json'
        self.items = [{'id': 'front', 'name': 'Front', 'source': 'rtsp://user:secret@192.0.2.1/live'}]
        self.config.write_text(json.dumps(self.items))
        env = patch.dict(os.environ, {'DASHBOARD_CAMERAS_FILE': str(self.config)})
        env.start(); self.addCleanup(env.stop)
        import dashboard_app
        self.client = dashboard_app.app.test_client()

    def test_public_list_omits_sources_and_credentials(self):
        response = self.client.get('/api/cameras')
        self.assertEqual(response.json, {'cameras': [{'id': 'front', 'name': 'Front', 'url': '/telemetry/video/front/'}]})
        self.assertNotIn(b'secret', response.data)

    def test_invalid_config_is_sanitized(self):
        self.config.write_text('[{"source":"rtsp://user:secret@host"}]')
        response = self.client.get('/api/cameras')
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(b'secret', response.data)
        for source in ['https://host/live', 'rtsp://host:invalid/live']:
            self.items[0]['source'] = source
            self.config.write_text(json.dumps(self.items))
            with self.assertRaises(ValueError): cameras()

    def test_relay_has_only_explicit_read_paths(self):
        config = relay_config(cameras())
        self.assertFalse(config['api'])
        self.assertFalse(config['rtsp'])
        self.assertFalse(config['hls'])
        self.assertEqual(config['webrtcAddress'], '127.0.0.1:9001')
        self.assertEqual(config['authInternalUsers'][0]['permissions'], [{'action': 'read', 'path': 'front'}])
        self.assertEqual(config['paths']['front']['source'], self.items[0]['source'])

    def test_proxy_restricts_routes_and_origins(self):
        for path in ['/video/unknown/', '/video/front/publish', '/video/front/api/config', '/video/front/whep']:
            self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.post('/video/front/whep', headers={'Origin': 'http://other-host'}).status_code, 403)
        self.assertEqual(self.client.post('/video/front/whep', data=b'x'*65537).status_code, 413)

    def test_whep_proxy_rewrites_session_location(self):
        class Reply(io.BytesIO):
            status = 201
            headers = {'Content-Type': 'application/sdp', 'Location': '/front/whep/abc-123', 'ETag': 'test'}
        with patch('video.build_opener') as opener:
            opener.return_value.open.return_value = Reply(b'v=0')
            response = self.client.post('/video/front/whep', data='v=0', content_type='application/sdp',
                                        headers={'Authorization': 'Basic console-secret'})
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.headers['Location'], '/telemetry/video/front/whep/abc-123')
            forwarded = opener.return_value.open.call_args.args[0]
            self.assertEqual(forwarded.full_url, 'http://127.0.0.1:9001/front/whep')
            self.assertIsNone(forwarded.get_header('Authorization'))
        with patch('video.build_opener') as opener:
            opener.return_value.open.side_effect = URLError('relay offline')
            self.assertEqual(self.client.get('/video/front/').status_code, 503)
