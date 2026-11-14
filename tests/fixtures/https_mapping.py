"""Verify the running app uses Supervisor's external port, still listening on 443."""

from pathlib import Path
import subprocess
import sys

import requests

sys.path.insert(0, '/opt/ha-openwisp')
from run import read_options

port = int(sys.argv[1])
options = read_options()
assert options['https_port'] == port
suffix = f':{port}' if port != 443 else ''
canonical = f"https://{options['api_domain']}{suffix}"
assert options['external_url'] == canonical
pid = subprocess.check_output([
    'supervisorctl', '-c', '/opt/ha-openwisp/supervisord.conf', 'pid'], text=True).strip()
env = dict(entry.split(b'=', 1) for entry in Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in entry)
for key in ('HA_OPENWISP_EXTERNAL_URL', 'OPENWISP_CONTROLLER_API_HOST',
            'OPENWISP_MONITORING_API_BASEURL', 'OPENWISP_NETWORK_TOPOLOGY_API_BASEURL'):
    assert env[key.encode()].decode() == canonical, key
assert env[b'NGINX_SSL_PORT'] == str(port).encode()
assert b'SUPERVISOR_TOKEN' not in env
session = requests.Session()
session.trust_env = False
for domain in (options['api_domain'], options['dashboard_domain']):
    response = session.get(f'https://{domain}/admin/login/',
                           headers={'Host': domain + suffix}, verify='/ssl/fullchain.pem', timeout=15)
    assert response.status_code in (200, 302, 404), response.status_code
    policy = response.headers['Content-Security-Policy']
    assert f"https://{options['api_domain']}:{port}" in policy, policy
    assert f"https://{options['dashboard_domain']}:{port}" in policy, policy
assert 'listen 8443' not in Path('/run/ha-openwisp/nginx.conf').read_text()
print(f'PASS: Supervisor host port {port} reaches canonical URLs, running application environment and HTTPS browser policy; listener remains on 443')
