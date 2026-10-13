"""Private dashboard service. Caddy strips /telemetry before forwarding."""
import json
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, request, send_from_directory
from flask_sock import Sock
from sensorhub_data import SensorHubTelemetry
from telemetry_history import TelemetryHistory
from video import video

DIST = Path(__file__).resolve().parent / 'dist'
app = Flask(__name__, static_folder=None)
app.register_blueprint(video)
sock = Sock(app)
telemetry = TelemetryHistory(SensorHubTelemetry())
telemetry.start()
app.config['SOCK_SERVER_OPTIONS'] = {'ping_interval': 25}


@app.get('/api/health')
def health():
    return jsonify(ok=True, service='rover-dashboard')


@app.get('/api/telemetry')
def snapshot():
    return jsonify(telemetry.snapshot())


@app.get('/api/battery')
def battery():
    return jsonify(telemetry.snapshot()['battery'])


def live(ws):
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        ws.close()
        return
    while ws.connected:
        ws.send(json.dumps(telemetry.snapshot()))
        # Wait on the socket so peer close is detected even when no telemetry arrives.
        ws.receive(timeout=2)


sock.route('/ws')(live)


@app.get('/')
@app.get('/<path:path>')
def frontend(path=''):
    if path == 'video' or path.startswith('video/') or path == 'api' or path.startswith('api/') or path == 'ws' or path.startswith('ws/'):
        abort(404)
    if path in ('app.js', 'styles.css'):
        return send_from_directory(DIST, path)
    if Path(path).suffix:
        abort(404)
    return send_from_directory(DIST, 'index.html')
