"""Verify real workers, scheduler and management-network access in the app."""
import json
import os
from pathlib import Path
import pwd
import sys
import subprocess
import time

sys.path.insert(0, '/opt/ha-openwisp')
from run import read_options
from settings import upstream_environment
from service import configure_suffixes
credentials = json.loads(Path('/data/openwisp/secrets.json').read_text())
os.environ.update(upstream_environment(os.environ, read_options(), credentials))
os.environ.update(MODULE_NAME='dashboard', DJANGO_SETTINGS_MODULE='openwisp.settings')
sys.path.insert(0, '/opt/ha-openwisp/dashboard')
configure_suffixes()
import django
django.setup()
from django.core.cache import cache
from openwisp.celery import app


def completion(task):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        result = cache.get('https-result-' + task.id)
        if result:
            assert result['state'] == 'SUCCESS', result
            return result
        time.sleep(0.2)
    raise AssertionError('Worker task did not complete')


# Execute an unchanged upstream periodic task on the normal default queue.
task = app.send_task('openwisp_monitoring.check.tasks.run_checks', kwargs={'checks': []})
assert completion(task)['name'] == 'openwisp_monitoring.check.tasks.run_checks'
for queue in ('celery', 'network', 'monitoring', 'monitoring_checks'):
    task = app.send_task('https.management_probe', args=[sys.argv[1]], queue=queue)
    assert completion(task)['value'] == 'management-network-reachable\n', queue
stats = app.control.inspect(timeout=5).stats()
assert stats and len(stats) == 1, stats
worker_stats = next(iter(stats.values()))
assert worker_stats['pool']['max-concurrency'] == read_options()['celery_concurrency'], worker_stats['pool']
print('PASS: upstream task and all four queues complete; one worker has configured concurrency and reaches the management fixture')
assert 'run_checks' in app.conf.beat_schedule
beat = Path('/data/openwisp/celery')
deadline = time.monotonic() + 20
while not list(beat.glob('beat*')):
    assert time.monotonic() < deadline, 'Scheduler did not create persistent state'
    time.sleep(0.2)

# Actual listeners: only Nginx is reachable from outside the app.
listeners = {}
for row in Path('/proc/net/tcp').read_text().splitlines()[1:]:
    fields = row.split()
    if fields[3] == '0A':
        host, port = fields[1].split(':')
        if int(port, 16) in (443, 8000, 8001, 8002, 5432, 6379, 8086, 8088):
            listeners[int(port, 16)] = host
assert set(listeners) == {443, 8000, 8001, 8002, 5432, 6379, 8086, 8088}, listeners
assert listeners.pop(443) == '00000000'
assert set(listeners.values()) == {'0100007F'}
ipv6_listeners = {}
for row in Path('/proc/net/tcp6').read_text().splitlines()[1:]:
    fields = row.split()
    if fields[3] == '0A':
        host, port = fields[1].split(':')
        if int(port, 16) in (443, 8000, 8001, 8002, 5432, 6379, 8086, 8088):
            ipv6_listeners[int(port, 16)] = host
assert ipv6_listeners == {443: '0' * 32}, ipv6_listeners

# A real public upload and a private canary exercise Nginx permissions/routing.
media = Path('/data/openwisp/media/https-public.txt')
account = pwd.getpwnam('openwisp')
subprocess.run([sys.executable, '-c',
                "from pathlib import Path; Path('/data/openwisp/media/https-public.txt').write_text('public-upload')"],
               user=account.pw_uid, group=account.pw_gid, extra_groups=[], umask=0o027, check=True)
assert media.stat().st_gid == pwd.getpwnam('www-data').pw_gid
assert media.stat().st_mode & 0o777 == 0o640
Path('/data/openwisp/private/https-secret.txt').write_text('private-canary')
print('PASS: persistent scheduler, public upload permissions and loopback-only application/dependency listeners')
