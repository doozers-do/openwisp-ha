"""Real TLS headers and retained HSTS cache, alongside an HTTP-only HA fixture."""
import json
from pathlib import Path

import requests

options = json.loads(Path('/fixture/options.json').read_text())
client = requests.Session()
client.trust_env = False
client.verify = '/fixture/fullchain.pem'
for host in set((options['dashboard_domain'], options['api_domain'])):
    for path in ('/admin/login/', '/admin', '/missing/', '/private/secret',
                 '/static/admin/css/base.css', '/static/missing.css',
                 '/api/v1/controller/device/', '/ws/missing/'):
        response = client.get('https://' + host + path, allow_redirects=False, timeout=15)
        expected = ['max-age=0'] if host == 'homeassistant.local' else []
        assert response.raw.headers.getlist('Strict-Transport-Security') == expected, (host, path)
print('PASS: success, redirects, errors, API, static and WebSocket paths have exact HSTS policy')
