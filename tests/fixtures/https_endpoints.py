"""Sign in and exercise the app directly through its internal HTTPS server."""
import base64
import hashlib
import json
from pathlib import Path
import socket
import ssl

import requests

CA = '/fixture/fullchain.pem'
options = json.loads(Path('/fixture/options.json').read_text())
DASHBOARD = 'https://' + options['dashboard_domain']
API = 'https://' + options['api_domain']
session = requests.Session()
session.trust_env = False
session.verify = CA

response = session.get(DASHBOARD + '/admin/login/', timeout=15)
assert response.status_code == 200, response.status_code
csrf = session.cookies.get('openwisp_direct_csrf')
assert csrf and 'Secure' in response.headers['Set-Cookie']
response = session.post(DASHBOARD + '/admin/login/?next=/admin/', data={
    'username': options['admin_username'], 'password': options['admin_password'],
    'csrfmiddlewaretoken': csrf, 'next': '/admin/',
}, headers={'Referer': DASHBOARD + '/admin/login/'}, timeout=30)
assert response.status_code == 200 and response.url == DASHBOARD + '/admin/', response.url
assert 'Log out' in response.text and 'OpenWISP' in response.text
assert session.cookies.get('openwisp_direct_session'), 'Sign-in did not create a session'
if options['dashboard_domain'] == options['api_domain']:
    assert all(not cookie.domain_specified for cookie in session.cookies), 'Shared-host cookies must be host-only'
print('PASS: supplied administrator signs in over certificate-verified HTTPS; dashboard and secure session/CSRF cookies')

# Upstream API role and cross-subdomain login cookies must work together.
response = session.get(API + '/api/v1/controller/device/', timeout=15)
assert response.status_code == 200, (response.status_code, response.text[:300])
assert isinstance(response.json(), (dict, list))
anonymous = requests.Session()
anonymous.trust_env = False
anonymous.verify = CA
assert anonymous.get(API + '/api/v1/controller/device/', timeout=15).status_code in (401, 403)
assert session.get(API + '/api/v1/controller/device/', headers={'Origin': DASHBOARD}, timeout=15).headers['Access-Control-Allow-Origin'] == DASHBOARD
print('PASS: configured API hostname, authenticated controller API, anonymous rejection and dashboard CORS origin')

# Hashed/compressed collected assets and public/private files through Nginx.
manifest = json.loads(Path('/fixture/manifest.json').read_text())
asset = manifest['paths']['admin/css/base.css']
response = session.get(DASHBOARD + '/static/' + asset, headers={'Accept-Encoding': 'br'}, timeout=15, stream=True)
assert response.status_code == 200 and response.headers.get('Content-Encoding') == 'br'
assert response.raw.read()
assert session.get(DASHBOARD + '/media/https-public.txt', timeout=15).text == 'public-upload'
response = session.get(DASHBOARD + '/private/https-secret.txt', timeout=15)
assert response.status_code == 404 and 'private-canary' not in response.text
print('PASS: hashed Brotli static assets, public uploads and denial of direct private files')

# Real authenticated WebSocket upgrade directly through packaged Nginx, over trusted TLS.
context = ssl.create_default_context(cafile=CA)
with socket.create_connection((options['dashboard_domain'], 443), timeout=15) as raw:
    with context.wrap_socket(raw, server_hostname=options['dashboard_domain']) as connection:
        key = base64.b64encode(b'https-websocketkey').decode()
        cookie = '; '.join(f'{item.name}={item.value}' for item in session.cookies)
        connection.sendall((
            f"GET /ws/notification/ HTTP/1.1\r\nHost: {options['dashboard_domain']}\r\n"
            'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
            f'Sec-WebSocket-Key: {key}\r\nOrigin: {DASHBOARD}\r\nCookie: {cookie}\r\n\r\n'
        ).encode())
        data = b''
        while b'\r\n\r\n' not in data:
            data += connection.recv(4096)
        assert data.startswith(b'HTTP/1.1 101'), data
        accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest())
        assert accept.lower() in data.lower()
        # Masked close frame, status 1000.
        connection.sendall(b'\x88\x82\x01\x02\x03\x04\x02\xea')
print("PASS: authenticated notification WebSocket upgrades through the app's packaged HTTPS Nginx")
