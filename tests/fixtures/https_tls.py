"""Validate TLS inputs in an isolated container before starting application roles."""
import sys
from pathlib import Path
import subprocess
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization

sys.path.insert(0, '/opt/ha-openwisp')
from proxy import TLSConfigurationError, validate_certificate

options = dict(certfile='fullchain.pem', keyfile='privkey.pem',
               dashboard_domain='controller.example.org', api_domain='api.example.org')
validate_certificate(options)
Path('/ssl/broken.pem').write_text('invalid-pem')
subprocess.run(['openssl', 'genpkey', '-algorithm', 'RSA', '-pkeyopt', 'rsa_keygen_bits:2048',
                '-out', '/ssl/wrong-key.pem'], capture_output=True, check=True)
key = serialization.load_pem_private_key(Path('/ssl/privkey.pem').read_bytes(), password=None)
cert = x509.load_pem_x509_certificate(Path('/ssl/fullchain.pem').read_bytes())
expired = (x509.CertificateBuilder().subject_name(cert.subject).issuer_name(cert.issuer)
           .public_key(key.public_key()).serial_number(x509.random_serial_number())
           .not_valid_before(datetime.now(timezone.utc) - timedelta(days=2))
           .not_valid_after(datetime.now(timezone.utc) - timedelta(days=1))
           .add_extension(cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value, critical=False)
           .sign(key, hashes.SHA256()))
Path('/ssl/expired.pem').write_bytes(expired.public_bytes(serialization.Encoding.PEM))
for changes in ({'certfile': 'missing.pem'}, {'keyfile': 'missing.pem'},
                {'certfile': 'broken.pem'}, {'certfile': 'expired.pem'}, {'keyfile': 'wrong-key.pem'},
                {'api_domain': 'wrong.example.net'}):
    try:
        validate_certificate({**options, **changes})
    except TLSConfigurationError as error:
        assert 'PRIVATE KEY' not in str(error)
    else:
        raise AssertionError('Invalid TLS input was accepted')
Path('/ssl/expired.pem').unlink()
Path('/ssl/broken.pem').unlink()
Path('/ssl/wrong-key.pem').unlink()
print('PASS: valid TLS pair accepted; missing/malformed/mismatched files, expired certificates and wrong hostname rejected')
