"""Exercise curl's on-disk HSTS cache against disposable TLS/HTTP endpoints."""
from pathlib import Path
import subprocess
import tempfile


def check_recovery(certificate, app, http_fixture, old_policy):
    with tempfile.TemporaryDirectory() as temporary:
        cache = str(Path(temporary) / 'hsts')

        def fetch(url):
            return subprocess.run([
                'curl', '--silent', '--show-error', '--noproxy', '*',
                '--max-time', '10', '--cacert', str(certificate),
                '--hsts', cache, '--output', '/dev/null',
                '--write-out', '%{url_effective} %{http_code}',
                '--connect-to', 'homeassistant.local:443:' + app,
                '--connect-to', 'homeassistant.local:8123:' + http_fixture,
                '--connect-to', 'homeassistant.local:9443:' + old_policy, url,
            ], capture_output=True, text=True)

        http = 'http://homeassistant.local:8123/'
        assert fetch(http).stdout == http + ' 200'
        assert fetch('https://homeassistant.local:9443/').returncode == 0
        broken = fetch(http)
        assert broken.returncode != 0 and broken.stdout == 'https://homeassistant.local:8123/ 000'
        assert fetch('https://homeassistant.local/admin/login/').returncode == 0
        for _ in range(2):
            assert fetch(http).stdout == http + ' 200'
            assert fetch('https://homeassistant.local/admin/login/').returncode == 0
            assert fetch(http).stdout == http + ' 200'
    print('PASS: old HSTS breaks HTTP 8123; recovery clears it; both visit orders retain HTTP across clients', flush=True)
