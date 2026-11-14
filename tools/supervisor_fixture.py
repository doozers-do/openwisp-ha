"""Start the Supervisor self-info fixture on a disposable Docker network."""

import json
from pathlib import Path

from image_lifecycle_check import docker

ROOT = Path(__file__).resolve().parents[1]
TOKEN_ENV = ('-e', 'SUPERVISOR_TOKEN=disposable-supervisor-token')


def set_https_mapping(fixture, port):
    (Path(fixture) / 'supervisor-info.json').write_text(json.dumps(
        dict(result='ok', data={'network': {'443/tcp': port}})))


def start_supervisor(image, network, name, fixture):
    set_https_mapping(fixture, 443)
    docker('run', '-d', '--platform', 'linux/amd64', '--network', network,
           '--network-alias', 'supervisor', '--name', name,
           '-v', f'{fixture}:/fixture:ro',
           '-v', f'{ROOT / "tests/fixtures/supervisor_info.py"}:/tmp/supervisor_info.py:ro',
           '--entrypoint', 'python', image, '/tmp/supervisor_info.py')
    # A second interpreter may start before the server; retry inside the fixture.
    docker('exec', name, 'python', '-c',
           "import socket, time\n"
           "for attempt in range(50):\n"
           " try:\n"
           "  socket.create_connection(('127.0.0.1', 80), timeout=1).close(); break\n"
           " except OSError: time.sleep(.1)\n"
           "else: raise RuntimeError('Supervisor fixture did not start')")
