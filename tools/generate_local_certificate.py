#!/usr/bin/env python3
"""Create installation certificate files outside the repository using OpenSSL."""
import argparse
import os
import shutil
import sys
from pathlib import Path
import subprocess
import tempfile


# Apple private-CA TLS limit: https://support.apple.com/en-us/103769
# Boundary-checked with native security verify-cert on macOS 26.5.2.
SERVER_VALIDITY_DAYS = 825
# The TLS leaf limit does not apply to its CA trust anchor.
CA_NO_EXPIRY = "99991231235959Z"


def openssl(*args):
    result = subprocess.run(['openssl', *map(str, args)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise RuntimeError(f'OpenSSL {args[0]} failed; check file permissions and OpenSSL installation')
    return result.stdout


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path.home() / 'openwisp-certs/homeassistant.local')
    parser.add_argument('--renew', action='store_true', help='replace server files; retain the CA key and extend a legacy CA to no expiry')
    args = parser.parse_args()
    destination = args.output.expanduser().resolve()
    repository = Path(__file__).resolve().parents[1]
    if destination == repository or repository in destination.parents:
        parser.error('certificate files must be generated outside the repository')
    os.umask(0o077)
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination.chmod(0o700)
    ca, ca_key = destination / 'rootCA.pem', destination / 'rootCA-key.pem'
    chain = destination / 'openwisp-homeassistant-fullchain.pem'
    key = destination / 'openwisp-homeassistant-privkey.pem'
    if ca.exists() != ca_key.exists():
        parser.error('incomplete CA pair; restore the missing file without replacing the existing one')
    if not args.renew and (chain.exists() or key.exists()):
        parser.error('server files already exist; use --renew to replace them while retaining the CA')
    with tempfile.TemporaryDirectory(prefix='certificate-', dir=destination) as temporary:
        work = Path(temporary)
        config = work / 'openssl.cnf'
        (work / 'index.txt').touch()
        (work / 'serial').write_text(os.urandom(16).hex() + '\n')
        config.write_text(f'''[ca]
default_ca = local_ca
[local_ca]
database = "{work}/index.txt"
serial = "{work}/serial"
new_certs_dir = "{work}"
certificate = "{work}/signing-ca.pem"
private_key = "{work}/ca-key.pem"
default_md = sha256
policy = ca_policy
unique_subject = no
[ca_policy]
commonName = supplied
[req]
prompt = no
distinguished_name = ca_name
x509_extensions = ca_extensions
[ca_name]
CN = OpenWISP homeassistant.local local CA
[ca_extensions]
basicConstraints = critical,CA:TRUE
keyUsage = critical,keyCertSign,cRLSign
subjectKeyIdentifier = hash
[server_extensions]
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment
extendedKeyUsage = serverAuth
subjectAltName = DNS:homeassistant.local
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
''')
        if ca.exists():
            if openssl('x509', '-in', ca, '-noout', '-pubkey') != openssl('pkey', '-in', ca_key, '-pubout'):
                raise RuntimeError('CA key mismatch; restore the matching CA pair')
            shutil.copy2(ca_key, work / 'ca-key.pem')
            shutil.copy2(ca, work / 'signing-ca.pem')
        else:
            openssl('genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:3072',
                    '-out', work / 'ca-key.pem')
            # OpenSSL ca needs an initial signing certificate. This temporary
            # bootstrap certificate is replaced before any output is installed.
            openssl('req', '-new', '-x509', '-key', work / 'ca-key.pem', '-sha256', '-days', '1',
                    '-config', config, '-out', work / 'signing-ca.pem')
        ca_updated = 'notAfter=Dec 31 23:59:59 9999 GMT' not in openssl(
            'x509', '-in', work / 'signing-ca.pem', '-noout', '-enddate')
        if ca_updated:
            openssl('req', '-new', '-key', work / 'ca-key.pem', '-config', config,
                    '-out', work / 'ca.csr')
            openssl('ca', '-batch', '-notext', '-selfsign', '-config', config,
                    '-enddate', CA_NO_EXPIRY, '-extensions', 'ca_extensions',
                    '-in', work / 'ca.csr', '-out', work / 'new-ca.pem')
            (work / 'new-ca.pem').replace(work / 'signing-ca.pem')
        openssl('req', '-new', '-newkey', 'rsa:2048', '-nodes', '-sha256',
                '-subj', '/CN=homeassistant.local', '-keyout', work / 'key.pem', '-out', work / 'server.csr')
        openssl('ca', '-batch', '-notext', '-config', config, '-days', str(SERVER_VALIDITY_DAYS),
                '-extensions', 'server_extensions', '-in', work / 'server.csr', '-out', work / 'server.pem')
        openssl('verify', '-CAfile', work / 'signing-ca.pem', work / 'server.pem')
        if openssl('x509', '-in', work / 'server.pem', '-noout', '-pubkey') != openssl('pkey', '-in', work / 'key.pem', '-pubout'):
            raise RuntimeError('Generated server key mismatch')
        (work / 'chain.pem').write_bytes((work / 'server.pem').read_bytes() + (work / 'signing-ca.pem').read_bytes())
        if sys.platform == 'darwin':
            result = subprocess.run([
                'security', 'verify-cert', '-L', '-c', str(work / 'server.pem'),
                '-r', str(work / 'signing-ca.pem'), '-p', 'ssl', '-n', 'homeassistant.local',
            ], capture_output=True, text=True, timeout=60)
            if result.returncode:
                raise RuntimeError('macOS TLS certificate verification failed; existing files were retained')
            print('macOS TLS certificate verification successful (explicit CA; keychain unchanged).')
        if ca_updated:
            (work / 'ca-key.pem').replace(ca_key)
            (work / 'signing-ca.pem').replace(ca)
            print('New or extended CA certificate: import the updated rootCA.pem on clients.')
        (work / 'key.pem').replace(key)
        (work / 'chain.pem').replace(chain)
    for public in (ca, chain):
        public.chmod(0o644)
    for private in (ca_key, key):
        private.chmod(0o600)
    print(f'Created certificate files for homeassistant.local in {destination}')
    print('Copy to Home Assistant ssl: ' + chain.name + ', ' + key.name)
    print('Trust on clients: rootCA.pem. Keep rootCA-key.pem private on this computer.')
    print(openssl('x509', '-in', chain, '-noout', '-dates'), end='')


if __name__ == '__main__':
    main()
