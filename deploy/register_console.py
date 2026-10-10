"""Prepare idempotent Caddy routing and registration; never replace a custom route."""
import argparse
import json
from pathlib import Path
import re

ROUTE = '''    redir /telemetry /telemetry/ 308

    handle_path /telemetry/* {
        reverse_proxy 127.0.0.1:9000
    }

'''
MANIFEST = {'id': 'telemetry', 'name': 'Rover Dashboard',
            'description': 'Live telemetry for this rover', 'adapter': 'external',
            'web_path': '/telemetry/', 'apply_enabled': False}


def configure(text):
    # Work on the documented Rover Console layout, not an arbitrary multi-site file.
    anchors = list(re.finditer(r'(?m)^    handle \{\s*\n\s*root \* /opt/rover-console/frontend/dist\s*$', text))
    if len(anchors) != 1:
        raise ValueError('Cannot identify the final Rover Console frontend handler; no files changed')
    if re.search(r'(?m)^\s*[^#\n]*?/telemetry', text):
        redirect = re.search(r'(?m)^\s*redir /telemetry /telemetry/ 308\s*$', text)
        handler = re.search(r'handle_path /telemetry/\*\s*\{\s*reverse_proxy 127\.0\.0\.1:9000\s*\}', text)
        if not redirect or not handler or handler.end() > anchors[0].start():
            raise ValueError('An existing custom telemetry route needs manual review; no files changed')
        return text
    return text[:anchors[0].start()] + ROUTE + text[anchors[0].start():]


def prepare(config_path, manifest_path, output):
    for path in (config_path, manifest_path):
        if path.is_symlink():
            raise ValueError(f'Refusing symlink: {path}')
    text = configure(config_path.read_text())
    if manifest_path.exists() and json.loads(manifest_path.read_text()) != MANIFEST:
        raise ValueError('An existing telemetry registration differs; review it before installation')
    output.mkdir(parents=True, exist_ok=True)
    (output / 'Caddyfile').write_text(text)
    (output / 'telemetry.json').write_text(json.dumps(MANIFEST, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, default=Path('/etc/rover-console/Caddyfile'))
    parser.add_argument('--manifest', type=Path, default=Path('/etc/rover-console/apps/telemetry.json'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(args.config, args.manifest, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(1, f'{exc}\n')
