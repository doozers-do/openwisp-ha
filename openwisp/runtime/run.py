#!/usr/bin/env python3
"""Prepare private state and give the internal process supervisor one lifecycle."""

import json
import os
from pathlib import Path
import pwd
import secrets
import signal
import subprocess
import sys

from proxy import TLSConfigurationError
from settings import OptionError, upstream_environment, validate_options

STATE = Path("/data/openwisp")
RUNTIME = Path("/run/ha-openwisp")
LEGACY_SECRET_KEYS = {"django", "redis", "database", "instance"}
SECRET_KEYS = LEGACY_SECRET_KEYS | {"influxdb"}


def environment(credentials, timeout):
    """Compatibility helper used by the in-image lifecycle check."""
    options = validate_options({
        "dashboard_domain": "dashboard.example.org", "api_domain": "api.example.org",
        "admin_username": "test-admin", "admin_email": "admin@example.org",
        "admin_password": "dummy-admin-password", "django_secret_key": credentials["django"],
        "runtime_mode": "production", "startup_timeout": timeout,
    })
    return upstream_environment(os.environ, options, credentials)


class StartupError(Exception):
    pass


def read_options():
    try:
        value = json.loads(Path("/data/options.json").read_text())
    except (OSError, ValueError):
        raise StartupError("/data/options.json must contain valid JSON") from None
    try:
        return validate_options(value)
    except OptionError as error:
        raise StartupError(str(error)) from None


def save_credentials(credentials, secret_file):
    temporary = STATE / "secrets.new"
    if temporary.is_symlink():
        raise StartupError("/data/openwisp/secrets.new must not be a symlink")
    with open(temporary, "w", opener=lambda name, flags: os.open(name, flags, 0o600)) as output:
        os.chmod(temporary, 0o600)
        json.dump(credentials, output)
        output.flush()
        os.fsync(output.fileno())
    temporary.replace(secret_file)
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def private_directory(path, owner, mode=0o700):
    if path.is_symlink():
        raise StartupError(f"{path} must not be a symlink")
    path.mkdir(mode=mode, exist_ok=True)
    account = pwd.getpwnam(owner)
    os.chown(path, account.pw_uid, account.pw_gid)
    os.chmod(path, mode)


def private_config(name, contents, owner):
    path = RUNTIME / name
    path.write_text(contents)
    account = pwd.getpwnam(owner)
    os.chown(path, account.pw_uid, account.pw_gid)
    os.chmod(path, 0o600)


def prepare_state():
    if not Path("/data").is_dir():
        raise StartupError("/data must be mounted as persistent app storage")
    if STATE.is_symlink():
        raise StartupError("/data/openwisp must not be a symlink")
    STATE.mkdir(mode=0o751, exist_ok=True)
    os.chmod(STATE, 0o751)
    secret_file = STATE / "secrets.json"
    if secret_file.is_symlink():
        raise StartupError("/data/openwisp/secrets.json must not be a symlink")
    # A partially written or damaged existing file must fail rather than reset keys.
    if not secret_file.exists():
        if any(STATE.iterdir()):
            raise StartupError("/data/openwisp/secrets.json is missing beside existing state; preserve it for recovery")
        generated = {key: secrets.token_hex(32) for key in SECRET_KEYS}
        save_credentials(generated, secret_file)
    os.chmod(secret_file, 0o600)
    try:
        credentials = json.loads(secret_file.read_text())
    except (OSError, ValueError):
        raise StartupError("/data/openwisp/secrets.json is unreadable or damaged; preserve it for recovery") from None
    if not isinstance(credentials, dict) or set(credentials) not in (SECRET_KEYS, LEGACY_SECRET_KEYS):
        raise StartupError("/data/openwisp/secrets.json has invalid keys; preserve it for recovery")
    if any(not isinstance(value, str) or len(value) != 64 or
           any(char not in "0123456789abcdef" for char in value)
           for value in credentials.values()):
        raise StartupError("/data/openwisp/secrets.json has invalid values; preserve it for recovery")
    if set(credentials) == LEGACY_SECRET_KEYS:
        credentials["influxdb"] = secrets.token_hex(32)
        save_credentials(credentials, secret_file)
    for name, owner, mode in (
        ("redis", "redis", 0o700), ("postgres", "postgres", 0o700),
        ("influxdb", "influxdb", 0o700), ("media", "openwisp", 0o2750),
        ("private", "openwisp", 0o700), ("ssh", "openwisp", 0o700),
        ("static", "openwisp", 0o755), ("celery", "openwisp", 0o700),
    ):
        private_directory(STATE / name, owner, mode)
    # Setgid media lets unprivileged Nginx read public uploads without opening
    # private application trees or granting it the upstream primary group (root).
    os.chown(STATE / "media", pwd.getpwnam("openwisp").pw_uid, pwd.getpwnam("www-data").pw_gid)
    RUNTIME.mkdir(mode=0o755, parents=True, exist_ok=True)
    os.chmod(RUNTIME, 0o755)
    for marker in ("failure", "initialization/complete"):
        (RUNTIME / marker).unlink(missing_ok=True)
    private_directory(RUNTIME / "postgresql", "postgres")
    private_directory(RUNTIME / "initialization", "openwisp")
    private_config("redis.conf",
        "bind 127.0.0.1\nport 6379\nprotected-mode yes\ndaemonize no\n"
        "dir /data/openwisp/redis\ndbfilename dump.rdb\n"
        "save 3600 1\nsave 300 100\nsave 60 10000\n"
        f"requirepass {credentials['redis']}\n", "redis"
    )
    private_config("postgres.password", credentials["database"], "postgres")
    private_config("pg_hba.conf",
                   "local all all peer\nhost all all 127.0.0.1/32 scram-sha-256\n", "postgres")
    private_config("postgresql.conf",
                   "listen_addresses = '127.0.0.1'\nport = 5432\n"
                   "unix_socket_directories = '/run/ha-openwisp/postgresql'\n"
                   "hba_file = '/run/ha-openwisp/pg_hba.conf'\n"
                   "shared_buffers = '32MB'\nmax_connections = 40\n"
                   "password_encryption = 'scram-sha-256'\n"
                   "log_min_error_statement = panic\n", "postgres")
    private_config("influxdb.conf",
                   'bind-address = "127.0.0.1:8088"\nreporting-disabled = true\n'
                   '[meta]\n  dir = "/data/openwisp/influxdb/meta"\n'
                   '[data]\n  dir = "/data/openwisp/influxdb/data"\n'
                   '  wal-dir = "/data/openwisp/influxdb/wal"\n  query-log-enabled = false\n'
                   '[http]\n  bind-address = "127.0.0.1:8086"\n  auth-enabled = true\n'
                   '  log-enabled = false\n[monitor]\n  store-enabled = false\n', "influxdb")
    return credentials


def run():
    os.umask(0o077)
    options = read_options()
    credentials = prepare_state()
    from proxy import configure_proxy
    configure_proxy(options)
    stopped = False
    child = None

    def stop(*_):
        nonlocal stopped
        stopped = True
        if child is not None and child.poll() is None:
            child.terminate()

    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, stop)
    print("OpenWISP: starting internal PostgreSQL/PostGIS, Redis and InfluxDB; "
          "upstream initialization precedes application startup", flush=True)
    child = subprocess.Popen([
        "supervisord", "--nodaemon", "--configuration", "/opt/ha-openwisp/supervisord.conf",
    ], env=upstream_environment(os.environ, options, credentials))
    if stopped:
        child.terminate()
    result = child.wait()
    if (RUNTIME / "failure").exists():
        print("OpenWISP ERROR: a required internal process failed; persistent data retained",
              file=sys.stderr, flush=True)
        return 1
    if result:
        return 1
    print("OpenWISP STOPPED: all internal services stopped cleanly", flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (StartupError, TLSConfigurationError) as error:
        print(f"OpenWISP ERROR: {error}", file=sys.stderr, flush=True)
        sys.exit(1)
    except OSError:
        print("OpenWISP ERROR: cannot prepare /data/openwisp or start the process supervisor",
              file=sys.stderr, flush=True)
        sys.exit(1)
