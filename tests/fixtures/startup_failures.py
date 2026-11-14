"""Preflight failures and legacy credential migration in a disposable container."""

import json
from pathlib import Path
import pwd
import subprocess
import sys

sys.path.insert(0, "/opt/ha-openwisp")


def readiness(label):
    from initialize import InitializationError, wait_for
    if label == "Redis":
        import redis
        from redis.backoff import NoBackoff
        from redis.retry import Retry
        client = redis.Redis(host="127.0.0.1", socket_timeout=1, socket_connect_timeout=1,
                             retry=Retry(NoBackoff(), 0))
    else:
        from influxdb import InfluxDBClient
        client = InfluxDBClient(host="127.0.0.1", timeout=1, retries=1)
    try:
        wait_for(label, client.ping)
    except InitializationError as error:
        print(str(error), file=sys.stderr)
        return 1
    raise AssertionError("Unavailable dependency was considered ready")


def preflight():
    from run import LEGACY_SECRET_KEYS, STATE, StartupError, prepare_state
    Path("/data").mkdir(exist_ok=True)
    STATE.mkdir()
    credentials = {key: "a" * 64 for key in LEGACY_SECRET_KEYS}
    secret_file = STATE / "secrets.json"
    secret_file.write_text(json.dumps(credentials))
    upgraded = prepare_state()
    assert all(upgraded[key] == credentials[key] for key in LEGACY_SECRET_KEYS)
    assert len(upgraded["influxdb"]) == 64
    assert prepare_state() == upgraded, "Subsequent startup changed credentials"
    secret_file.rename(STATE / "retained-secrets.json")
    try:
        prepare_state()
    except StartupError as error:
        assert "missing beside existing state" in str(error)
        assert not secret_file.exists(), "Missing credentials were regenerated"
    else:
        raise AssertionError("Missing credentials were accepted")
    (STATE / "retained-secrets.json").rename(secret_file)
    account = pwd.getpwnam("postgres")
    sentinel = STATE / "postgres/partial-initialization"
    sentinel.write_text("retain-this-state")
    for version in (None, "16"):
        if version:
            (STATE / "postgres/PG_VERSION").write_text(version)
        result = subprocess.run([sys.executable, "/opt/ha-openwisp/postgres.py"],
                                user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                                text=True, capture_output=True, timeout=10)
        assert result.returncode == 1
        expected = "nonempty without PG_VERSION" if version is None else "17-compatible storage"
        assert expected in result.stderr, result.stderr
        assert sentinel.read_text() == "retain-this-state"
    secret_file.write_text("{}")
    try:
        prepare_state()
    except StartupError:
        assert secret_file.read_text() == "{}", "Damaged credentials were replaced"
    else:
        raise AssertionError("Invalid credentials were accepted")
    print("PASS: legacy credentials retained, new credential stable, missing/damaged/partial/incompatible state preserved")


if __name__ == "__main__":
    if sys.argv[1:]:
        sys.exit(readiness(sys.argv[1]))
    preflight()
