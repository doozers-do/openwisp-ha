"""Force each address family: trusted TLS routes and unknown SNI rejection."""
import json
from pathlib import Path
import socket
import ssl

options = json.loads(Path('/fixture/options.json').read_text())
context = ssl.create_default_context(cafile='/fixture/fullchain.pem')
for family in (socket.AF_INET, socket.AF_INET6):
    for hostname in dict.fromkeys((options['dashboard_domain'], options['api_domain'])):
        address = socket.getaddrinfo(hostname, 443, family, socket.SOCK_STREAM)[0][4]
        with socket.socket(family, socket.SOCK_STREAM) as raw:
            raw.settimeout(15)
            raw.connect(address)
            with context.wrap_socket(raw, server_hostname=hostname) as connection:
                connection.sendall((
                    f'GET /admin/login/ HTTP/1.1\r\nHost: {hostname}\r\nConnection: close\r\n\r\n'
                ).encode())
                response = b''
                while b'\r\n' not in response:
                    chunk = connection.recv(4096)
                    assert chunk, 'Connection closed before HTTP response'
                    response += chunk
                assert response.startswith(b'HTTP/1.1 200'), response[:200]
        # Disable client verification only here: rejection must come from Nginx,
        # rather than the client rejecting a certificate for the unknown name.
        rejection = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        rejection.check_hostname = False
        rejection.verify_mode = ssl.CERT_NONE
        with socket.socket(family, socket.SOCK_STREAM) as raw:
            raw.settimeout(15)
            raw.connect(address)
            try:
                with rejection.wrap_socket(raw, server_hostname='unknown.example.net'):
                    raise AssertionError('Unknown SNI completed a TLS handshake')
            except ssl.SSLError:
                pass
    print(f'PASS: {family.name} certificate-verified login for configured hosts; unknown SNI rejected')
