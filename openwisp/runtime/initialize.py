"""Bound dependency readiness and run the pinned dashboard's initialization steps."""

import os
from pathlib import Path
import subprocess
import sys
import time

import psycopg
import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError, InfluxDBServerError
from requests.exceptions import RequestException

TIMEOUT = int(os.environ["HA_OPENWISP_STARTUP_TIMEOUT"])
COMPLETE = Path("/run/ha-openwisp/initialization/complete")


class InitializationError(Exception):
    pass


def wait_for(label, probe):
    deadline = time.monotonic() + TIMEOUT
    while True:
        try:
            probe()
            print(f"OpenWISP: internal {label} ready", flush=True)
            return
        except (psycopg.Error, redis.RedisError, RequestException,
                InfluxDBClientError, InfluxDBServerError, OSError):
            if time.monotonic() >= deadline:
                raise InitializationError(f"internal {label} unavailable after {TIMEOUT}s") from None
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))


def postgres_ready():
    with psycopg.connect(host="127.0.0.1", port=5432, user="admin", dbname="postgres",
                        password=os.environ["DB_PASS"], connect_timeout=1):
        pass


def initialize_databases():
    with psycopg.connect(host="127.0.0.1", port=5432, user="admin", dbname="postgres",
                        password=os.environ["DB_PASS"], connect_timeout=1, autocommit=True) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname = 'openwisp_db'").fetchone():
            connection.execute("CREATE DATABASE openwisp_db")
    with psycopg.connect(host="127.0.0.1", port=5432, user="admin", dbname="openwisp_db",
                        password=os.environ["DB_PASS"], connect_timeout=1) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS postgis")
    # InfluxDB allows creation of its first admin with auth enabled. On later
    # starts authenticate first; a password mismatch must never reset users/data.
    client = InfluxDBClient(host="127.0.0.1", port=8086, username="admin",
                            password=os.environ["INFLUXDB_PASS"], timeout=1, retries=1)
    try:
        try:
            client.get_list_database()
        except InfluxDBClientError as error:
            if error.code not in (401, 403):
                raise
            client.create_user("admin", os.environ["INFLUXDB_PASS"], admin=True)
        client.get_list_database()
        client.create_database("openwisp")
    finally:
        client.close()


def step(label, *command, umask=0o077):
    print(f"OpenWISP: initialization step: {label}", flush=True)
    try:
        # Capture output: upstream exceptions can contain credentials or key data.
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                timeout=300, umask=umask)
    except (OSError, subprocess.TimeoutExpired):
        raise InitializationError(f"{label} failed or exceeded 300s; existing data retained") from None
    if result.returncode:
        raise InitializationError(f"{label} failed; existing data retained")


def main():
    os.environ["MODULE_NAME"] = "dashboard"
    os.environ["DJANGO_SETTINGS_MODULE"] = "openwisp.settings"
    os.chdir("/opt/ha-openwisp/dashboard")
    # Make the role's upstream files importable for in-process management calls.
    sys.path.insert(0, os.getcwd())
    from service import configure_suffixes
    configure_suffixes()
    client = redis.Redis(host="127.0.0.1", password=os.environ["REDIS_PASS"],
                         socket_timeout=1, socket_connect_timeout=1,
                         retry=Retry(NoBackoff(), 0))
    wait_for("PostgreSQL", postgres_ready)
    wait_for("Redis", client.ping)
    # In this pinned client, retries=0 means retry forever; 1 means one attempt.
    influx = InfluxDBClient(host="127.0.0.1", port=8086, timeout=1, retries=1)
    wait_for("InfluxDB", influx.ping)
    influx.close()
    try:
        initialize_databases()
    except (psycopg.Error, RequestException, InfluxDBClientError, InfluxDBServerError):
        raise InitializationError("database bootstrap failed; existing data retained") from None
    step("upstream migrations", sys.executable, "manage.py", "migrate", "--noinput")
    # Upstream's load_init_data.py creates admin/admin only if no superuser exists.
    # Seed supplied credentials first; never reset an existing administrator.
    step("administrator bootstrap", sys.executable, "/opt/ha-openwisp/initialize.py", "admin")
    key = Path(os.environ["SSH_PRIVATE_KEY_PATH"])
    public = Path(os.environ["SSH_PUBLIC_KEY_PATH"])
    if not key.exists() and public.exists():
        raise InitializationError("SSH private key missing beside existing public key; preserve /data/openwisp/ssh")
    if not key.exists():
        step("SSH key generation", "ssh-keygen", "-t", "ed25519", "-f", str(key), "-N", "")
    if not public.exists():
        # Keep a partial installation intact for explicit repair, never rotate keys.
        raise InitializationError("SSH public key missing; preserve /data/openwisp/ssh for recovery")
    step("upstream initial data", sys.executable, "load_init_data.py")
    # Static files are regenerable. Force collection if this tree was removed,
    # even if Redis still holds upstream's dependency hash from an earlier start.
    # The package adapts pinned frontend source; upstream's dependency hash does
    # not notice wrapper upgrades. Recollect once per frontend revision.
    frontend_revision = Path('/data/openwisp/static/.ha-frontend-revision')
    if not Path("static/staticfiles.json").exists() or not frontend_revision.exists() or frontend_revision.read_text() != 'ingress-1':
        os.environ["COLLECTSTATIC_WHEN_DEPS_CHANGE"] = "False"
    step("upstream static collection", sys.executable, "collectstatic.py", umask=0o022)
    frontend_revision.write_text('ingress-1')
    client.save()
    COMPLETE.touch(mode=0o600)
    print("OpenWISP: upstream initialization complete; persistent services ready", flush=True)
    return 0


def admin():
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    User = get_user_model()
    if not User.objects.filter(is_superuser=True).exists():
        User.objects.create_superuser(os.environ["HA_OPENWISP_ADMIN_USERNAME"],
                                      os.environ["HA_OPENWISP_ADMIN_EMAIL"],
                                      os.environ["HA_OPENWISP_ADMIN_PASSWORD"])


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["admin"]:
            admin()
        else:
            sys.exit(main())
    except InitializationError as error:
        print(f"OpenWISP ERROR: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
    except Exception:
        print("OpenWISP ERROR: upstream initialization failed; existing data retained",
              file=sys.stderr, flush=True)
        sys.exit(1)
