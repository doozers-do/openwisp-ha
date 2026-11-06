#!/usr/bin/env python3
"""Qualify ingress through a disposable Supervisor-like proxy at its exact IP."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from image_lifecycle_check import docker, logs, state
from https_endpoints_check import ready
from tls_fixture import create_tls_fixture

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--keep', action='store_true', help='retain fixtures for debugging')
    parser.add_argument('--https', action='store_true', help='simulate HTTPS Home Assistant ingress')
    args = parser.parse_args()
    name = 'ha-openwisp-ingress-' + uuid.uuid4().hex[:8]
    network, proxy, image = name + '-net', name + '-proxy', 'ha-openwisp:ingress-dev'
    fixture = Path(tempfile.mkdtemp(prefix=name))
    try:
        docker('build', '--platform', 'linux/amd64', '-t', image, str(ROOT / 'openwisp'), timeout=600)
        docker('network', 'create', '--subnet', '172.30.32.0/24', network)
        routes = []
        for token, uid in (('one', 'a' * 32), ('two', 'a' * 32), ('denied', 'b' * 32),
                           ('limited', 'c' * 32), ('missing', '')):
            routes.append(f'''
            location /api/hassio_ingress/{token}/ {{
                proxy_pass http://app:8099/;
                proxy_set_header Host $http_host;
                proxy_set_header X-Forwarded-Proto $scheme;
                proxy_set_header X-Ingress-Path /api/hassio_ingress/{token};
                proxy_set_header X-Remote-User-Id "{uid}";
                proxy_http_version 1.1;
                proxy_set_header Upgrade $http_upgrade;
                proxy_set_header Connection "upgrade";
                proxy_hide_header Referrer-Policy;
                add_header Referrer-Policy no-referrer always;
            }}''')
        # Start proxy after app DNS exists; use a stopped container to reserve
        # its ephemeral host mapping, then inspect it after startup.
        tls = ''
        if args.https:
            create_tls_fixture(fixture)
            tls = ' ssl_certificate /fixture/fullchain.pem; ssl_certificate_key /fixture/privkey.pem;'
        listen = '8123 ssl' if args.https else '8123'
        (fixture / 'nginx.conf').write_text(f'events {{}}\nhttp {{ access_log off; server {{ listen {listen};' + tls
            + ''.join(routes) + '\nlocation / { return 404; } } }')
        # Allocate host port with a temporary proxy whose upstream DNS is runtime-resolved.
        text = (fixture / 'nginx.conf').read_text().replace('proxy_pass http://app:8099/;',
            'resolver 127.0.0.11; set $backend app;\n'
            'rewrite ^/api/hassio_ingress/[^/]+/(.*)$ /$1 break; proxy_pass http://$backend:8099;')
        (fixture / 'nginx.conf').write_text(text)
        docker('run', '-d', '--platform', 'linux/amd64', '--name', proxy,
               '--network', network, '--ip', '172.30.32.2', '-p', '127.0.0.1::8123',
               '-v', f'{fixture}:/fixture:ro', '--entrypoint', 'nginx', image,
               '-c', '/fixture/nginx.conf', '-g', 'daemon off;')
        port = docker('port', proxy, '8123').stdout.strip()
        origin = ('https://' if args.https else 'http://') + port
        options = dict(direct_https=False, ingress_enabled=True, ingress_origin=origin,
                       ingress_users=[dict(ha_user_id='a' * 32, openwisp_username='test-admin'),
                                      dict(ha_user_id='c' * 32, openwisp_username='operator')],
                       admin_username='test-admin', admin_email='admin@example.org',
                       admin_password='disposable-admin-password', django_secret_key='x' * 64,
                       runtime_mode='production', startup_timeout=20)
        (fixture / 'options.json').write_text(json.dumps(options))
        docker('run', '-d', '--platform', 'linux/amd64', '--name', name,
               '--network', network, '--network-alias', 'app',
               '-v', f'{fixture / "options.json"}:/data/options.json:ro', image)
        ready(name)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            result = docker('exec', name, 'supervisorctl', '-c', '/opt/ha-openwisp/supervisord.conf',
                            'status', 'ingress', check=False)
            if 'RUNNING' in result.stdout:
                break
            time.sleep(.5)
        assert 'RUNNING' in result.stdout, logs(name)
        seeded = docker('exec', '-i', name, 'python', '-', input=(ROOT / 'tests/fixtures/ingress_seed.py').read_text())
        print(seeded.stdout, end='', flush=True)
        info = json.loads(docker('inspect', name).stdout)[0]
        assert not info['HostConfig']['PortBindings']
        print('PASS: ingress-only startup without TLS files, public DNS, or published app ports', flush=True)
        # Same Docker network, different peer: even forged headers cannot enter.
        denied = docker('run', '--rm', '--platform', 'linux/amd64', '--network', network,
            '--entrypoint', 'python', image, '-c',
            "import requests; r=requests.get('http://app:8099/admin/', headers={'X-Remote-User-Id':'" + 'a' * 32 +
            "','X-Ingress-Path':'/api/hassio_ingress/one'}); assert r.status_code == 403; print('PASS: neighboring peer denied')")
        print(denied.stdout, end='', flush=True)
        browser = subprocess.run(['node', str(ROOT / 'tests/fixtures/ingress_browser.cjs'), origin],
                                 capture_output=True, text=True, timeout=180)
        print(browser.stdout, end='', flush=True)
        assert browser.returncode == 0, browser.stderr
        snapshot = docker('exec', name, 'sha256sum', '/data/openwisp/secrets.json').stdout
        docker('exec', name, 'supervisorctl', '-c', '/opt/ha-openwisp/supervisord.conf', 'signal', 'KILL', 'ingress')
        docker('wait', name, timeout=60)
        assert state(name)['ExitCode'] == 1
        docker('start', name)
        ready(name)
        assert docker('exec', name, 'sha256sum', '/data/openwisp/secrets.json').stdout == snapshot
        print('PASS: ingress failure stops the app; restart retains credentials', flush=True)
        docker('restart', name, timeout=120)
        ready(name)
        print('PASS: ingress-only restart preserves application startup', flush=True)
    except Exception:
        output = docker('logs', name, check=False)
        if output.returncode == 0:
            print((output.stdout + output.stderr)[-7000:])
        raise
    finally:
        if args.keep:
            print(f'Fixtures retained: {name}, {proxy}, {network}, {fixture}', flush=True)
        else:
            for container in (name, proxy):
                docker('rm', '-f', container, check=False)
            docker('network', 'rm', network, check=False)
            import shutil
            shutil.rmtree(fixture)


if __name__ == '__main__':
    main()
