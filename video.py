"""View-only camera configuration and loopback WebRTC signaling proxy."""
import argparse
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

from flask import Blueprint, Response, abort, jsonify, request

video = Blueprint('video', __name__)
DEFAULT_CONFIG = '/etc/rover-dashboard/cameras.json'


def cameras(path=None):
    path = Path(path or os.environ.get('DASHBOARD_CAMERAS_FILE', DEFAULT_CONFIG))
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text())
        assert isinstance(items, list) and len(items) <= 8
        ids = set()
        for item in items:
            assert isinstance(item, dict)
            assert isinstance(item.get('id'), str) and re.fullmatch(r'[a-z][a-z0-9_-]{0,31}', item['id'])
            assert item['id'] not in ids
            ids.add(item['id'])
            assert isinstance(item.get('name'), str) and 0 < len(item['name']) <= 80
            source = item.get('source')
            assert isinstance(source, str) and not any(ord(c) < 32 for c in source)
            url = urlsplit(source)
            assert url.scheme in ('rtsp', 'rtsps') and url.hostname and url.port != 0
        return items
    except (AssertionError, ValueError, TypeError, OSError):
        # Never include the credential-bearing source or parser exception.
        raise ValueError('Invalid camera configuration') from None


def relay_config(items):
    return {'logLevel': 'warn', 'rtsp': False, 'rtmp': False, 'hls': False, 'srt': False,
            'moq': False, 'api': False, 'metrics': False, 'playback': False,
            'webrtc': True, 'webrtcAddress': '127.0.0.1:9001',
            'webrtcLocalUDPAddress': ':8189',
            'webrtcAdditionalHosts': [h.strip() for h in os.environ.get('VIDEO_ADDITIONAL_HOSTS', '').split(',') if h.strip()],
            'authInternalUsers': [{'user': 'any', 'permissions': [{'action': 'read', 'path': item['id']} for item in items]}],
            'paths': {item['id']: {'source': item['source'], 'rtspTransport': 'tcp', 'sourceOnDemand': True}
                      for item in items}}


@video.get('/api/cameras')
def camera_list():
    try:
        items = cameras()
    except ValueError:
        return jsonify(cameras=[], error='Invalid camera configuration'), 503
    return jsonify(cameras=[{'id': item['id'], 'name': item['name'],
                             'url': f"/telemetry/video/{item['id']}/"} for item in items])


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@video.route('/video/<camera>/', defaults={'resource': ''}, methods=['GET'])
@video.route('/video/<camera>/<path:resource>', methods=['GET', 'POST', 'PATCH', 'DELETE', 'OPTIONS'])
def player(camera, resource):
    try:
        registered = {item['id'] for item in cameras()}
    except ValueError:
        abort(503)
    if camera not in registered:
        abort(404)
    allowed = ((resource in ('', 'reader.js') and request.method == 'GET')
               or (resource == 'whep' and request.method in ('POST', 'OPTIONS'))
               or (re.fullmatch(r'whep/[a-zA-Z0-9-]+', resource) and request.method in ('PATCH', 'DELETE', 'OPTIONS')))
    if not allowed:
        abort(404)
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        abort(403)
    if request.content_length and request.content_length > 65536:
        abort(413)
    body = request.stream.read(65537)
    if len(body) > 65536:
        abort(413)
    headers = {name: request.headers[name] for name in ('Content-Type', 'Accept', 'If-Match') if name in request.headers}
    upstream = Request(f'http://127.0.0.1:9001/{camera}/{resource}',
                       data=body if request.method in ('POST', 'PATCH') else None,
                       headers=headers, method=request.method)
    try:
        try:
            response = build_opener(ProxyHandler({}), NoRedirect()).open(upstream, timeout=12)
        except HTTPError as exc:
            response = exc
        with response:
            content = response.read(2 * 1024 * 1024)
            output_headers = {key: value for key, value in response.headers.items()
                              if key.lower() in ('content-type', 'etag', 'accept-patch', 'link')}
            location = response.headers.get('Location')
            if location:
                path = urlsplit(location).path
                if not path.startswith(f'/{camera}/'):
                    abort(502)
                output_headers['Location'] = '/telemetry/video' + path
            output_headers['Cache-Control'] = 'no-store'
            return Response(content, status=response.status, headers=output_headers)
    except (URLError, TimeoutError, OSError):
        return Response('Video relay unavailable. Check rover-dashboard-video.service.', status=503, content_type='text/plain')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', default=DEFAULT_CONFIG)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    try:
        config = relay_config(cameras(args.config))
        target = Path(args.output)
        target.write_text(json.dumps(config))  # JSON is valid YAML.
        target.chmod(0o600)
    except (ValueError, OSError):
        parser.exit(1, 'Cannot prepare video relay configuration; check cameras.json and file permissions.\n')
