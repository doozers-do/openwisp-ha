"""Disposable trusted certificate files for local app integration checks only."""
from pathlib import Path
import subprocess


def create_tls_fixture(directory):
    directory = Path(directory)
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '1',
                    '-keyout', str(directory / 'privkey.pem'), '-out', str(directory / 'fullchain.pem'),
                    '-subj', '/CN=controller.example.org', '-addext',
                    'subjectAltName=DNS:controller.example.org,DNS:api.example.org,DNS:dashboard.example.org,DNS:homeassistant.local'],
                   capture_output=True, check=True)
