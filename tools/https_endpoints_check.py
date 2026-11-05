#!/usr/bin/env python3
"""Build the app and test the complete app directly over its own HTTPS listener."""
import json
import argparse
from pathlib import Path
import tempfile
import time
import uuid

from image_lifecycle_check import docker, logs, state
from tls_fixture import create_tls_fixture
from hsts_check import check_recovery

ROOT = Path(__file__).resolve().parents[1]
APPLICATIONS = ('dashboard', 'api', 'websocket', 'celery', 'scheduler', 'nginx')


def ready(name):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        assert state(name)['Running'], logs(name)
        status = docker('exec', name, 'supervisorctl', '-c', '/opt/ha-openwisp/supervisord.conf',
                        'status', check=False).stdout
        running = {row.split()[0] for row in status.splitlines() if 'RUNNING' in row}
        if set(APPLICATIONS).issubset(running):
            return
        time.sleep(0.5)
    raise AssertionError('Application startup timed out:\n' + logs(name))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--local-host', action='store_true', help='test one homeassistant.local hostname')
    args = parser.parse_args()
    prefix = 'ha-openwisp-https-' + uuid.uuid4().hex[:10]
    image, name, client, network, volume = prefix + ':test', prefix, prefix + '-client', prefix + '-net', prefix + '-data'
    try:
        docker('build', '--platform', 'linux/amd64', '-t', image, str(ROOT / 'openwisp'), timeout=600)
        subnet = 'fdf0:' + uuid.uuid4().hex[:4] + ':' + uuid.uuid4().hex[:4] + '::/64'
        docker('network', 'create', '--ipv6', '--subnet', subnet, network)
        docker('volume', 'create', volume)
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            fixture = Path(temporary)
            options = dict(dashboard_domain='controller.example.org', api_domain='api.example.org',
                           admin_username='test-admin', admin_email='admin@example.org',
                           admin_password='disposable-admin-password', django_secret_key='x' * 64,
                           runtime_mode='production', startup_timeout=10, celery_concurrency=3)
            if args.local_host:
                options.update(dashboard_domain='homeassistant.local', api_domain='homeassistant.local')
            (fixture / 'options.json').write_text(json.dumps(options))
            (fixture / 'custom_django_settings.py').write_text("CELERY_IMPORTS = ('https_tasks',)\n")
            create_tls_fixture(fixture)
            tls = docker('run', '--rm', '-i', '--platform', 'linux/amd64', '--network', 'none',
                         '-v', f'{fixture}:/ssl', '--entrypoint', 'python', image, '-',
                         input=(ROOT / 'tests/fixtures/https_tls.py').read_text())
            print(tls.stdout, end='', flush=True)
            missing = docker('run', '--rm', '--platform', 'linux/amd64', '--network', 'none',
                             '-v', f'{fixture / "options.json"}:/data/options.json:ro', image, check=False)
            assert missing.returncode == 1 and 'certfile: file must exist' in missing.stderr
            assert 'supervisord' not in missing.stdout
            print('PASS: missing certificate blocks startup before services launch', flush=True)
            (fixture / 'nginx.conf').write_text('''events {}
http {
    access_log off;
    server { listen 8080; location / { return 200 "management-network-reachable\\n"; } }
    server { listen 8123; location / { return 200 "home-assistant-http\\n"; } }
    server {
        listen 9443 ssl;
        ssl_certificate /fixture/fullchain.pem;
        ssl_certificate_key /fixture/privkey.pem;
        add_header Strict-Transport-Security "max-age=31536000" always;
        location / { return 200 "old-policy-fixture\\n"; }
    }
}
''')
            docker('run', '-d', '--platform', 'linux/amd64', '--network', network, '--network-alias', 'app',
                   '--network-alias', options['dashboard_domain'], '--network-alias', options['api_domain'],
                   '--name', name, '-p', '127.0.0.1::443', '-v', f'{volume}:/data',
                   '-v', f'{fixture}:/ssl:ro',
                   '-v', f'{fixture / "options.json"}:/data/options.json:ro',
                   '-v', f'{fixture / "custom_django_settings.py"}:/opt/ha-openwisp/dashboard/openwisp/configuration/custom_django_settings.py:ro',
                   '-v', f'{ROOT / "tests/fixtures/https_tasks.py"}:/opt/ha-openwisp/dashboard/https_tasks.py:ro', image)
            ready(name)
            docker('run', '-d', '--platform', 'linux/amd64', '--network', network,
                   '--network-alias', 'management-fixture', '--name', client,
                   '-p', '127.0.0.1::8123', '-p', '127.0.0.1::9443',
                   '-v', f'{fixture}:/fixture:ro', '--entrypoint', 'nginx', image,
                   '-c', '/fixture/nginx.conf', '-g', 'daemon off;')
            inside = docker('exec', '-i', name, 'python', '-', 'http://management-fixture:8080/',
                            input=(ROOT / 'tests/fixtures/https_inside.py').read_text(), timeout=180)
            print(inside.stdout, end='', flush=True)
            dualstack = docker('exec', '-i', client, 'python', '-',
                              input=(ROOT / 'tests/fixtures/https_dualstack.py').read_text(), timeout=60)
            print(dualstack.stdout, end='', flush=True)
            docker('cp', f'{name}:/data/openwisp/static/staticfiles.json', str(fixture / 'manifest.json'))
            endpoints = docker('exec', '-i', client, 'python', '-',
                               input=(ROOT / 'tests/fixtures/https_endpoints.py').read_text(), timeout=120)
            print(endpoints.stdout, end='', flush=True)
            recovery = docker('exec', '-i', client, 'python', '-',
                              input=(ROOT / 'tests/fixtures/https_hsts.py').read_text(), timeout=60)
            print(recovery.stdout, end='', flush=True)
            if args.local_host:
                mappings = [docker('port', container, str(port)).stdout.strip()
                            for container, port in ((name, 443), (client, 8123), (client, 9443))]
                check_recovery(fixture / 'fullchain.pem', *mappings)
            info = json.loads(docker('inspect', name).stdout)[0]
            assert set(info['HostConfig']['PortBindings']) == {'443/tcp'}
            assert info['HostConfig']['NetworkMode'] == network
            assert not info['HostConfig']['Privileged'] and not info['HostConfig']['Devices']
            assert set(json.loads(docker('inspect', client).stdout)[0]['HostConfig']['PortBindings']) == {'8123/tcp', '9443/tcp'}
            print('PASS: only HTTPS port published; ordinary worker network; no VPN devices/privileges', flush=True)
            for role in ('celery', 'scheduler', 'nginx'):
                docker('exec', name, 'supervisorctl', '-c', '/opt/ha-openwisp/supervisord.conf', 'signal', 'KILL', role)
                docker('wait', name, timeout=60)
                assert state(name)['ExitCode'] == 1, logs(name)
                assert f'{role} reported PROCESS_STATE_EXITED' in logs(name)
                docker('start', name)
                ready(name)
            endpoints = docker('exec', '-i', client, 'python', '-',
                               input=(ROOT / 'tests/fixtures/https_endpoints.py').read_text(), timeout=120)
            print('PASS: required worker/scheduler/Nginx failure propagates; login/endpoints recover on restart', flush=True)
            docker('stop', '-t', '110', name, timeout=120)
            assert state(name)['ExitCode'] == 0, logs(name)
            output = logs(name)
            assert "killing '" not in output, 'Forced termination during shutdown'
            secrets = json.loads(docker('run', '--rm', '--platform', 'linux/amd64', '-v', f'{volume}:/data:ro',
                                       '--entrypoint', 'cat', image, '/data/openwisp/secrets.json').stdout)
            assert all(value not in output for value in (*secrets.values(), options['admin_password'], options['django_secret_key']))
            assert 'https-secret' not in output
            print('PASS: clean complete-service shutdown, persistent beat state and secret-safe logs', flush=True)
    except Exception:
        for container in (name, client):
            result = docker('logs', '--tail', '100', container, check=False)
            print(result.stdout + result.stderr, flush=True)
        raise
    finally:
        for container in (name, client):
            docker('rm', '-f', container, check=False)
        docker('volume', 'rm', volume, check=False)
        docker('network', 'rm', network, check=False)
        docker('image', 'rm', image, check=False)


if __name__ == '__main__':
    main()
