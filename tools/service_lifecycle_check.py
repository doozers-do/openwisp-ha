#!/usr/bin/env python3
"""Validate normal app startup, persistence and database interruption offline."""

import json
from pathlib import Path
import tempfile
import time
import uuid

from check_app_discovery import find_configs
from tls_fixture import create_tls_fixture
from image_lifecycle_check import docker, logs, state
from https_endpoints_check import ready as applications_ready


def wait_ready(name, count):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        output = logs(name)
        if output.count("Listening on TCP address 127.0.0.1:8002") >= count:
            assert output.count("upstream initialization complete") == count, output
            applications_ready(name)
            return
        assert state(name)["Running"], f"Startup failed:\n{output}"
        time.sleep(0.25)
    raise AssertionError(f"Startup exceeded 180s:\n{logs(name)}")


def main():
    root = Path(__file__).resolve().parents[1]
    assert find_configs(root) == ["openwisp/config.yaml"]
    prefix = "ha-openwisp-services-" + uuid.uuid4().hex[:10]
    image, volume, name = prefix + ":test", prefix + "-data", prefix
    inside = (root / "tests/fixtures/service_persistence.py").read_text()
    try:
        docker("build", "--platform", "linux/amd64", "-t", image, str(root / "openwisp"), timeout=600)
        started = time.monotonic()
        missing = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                         "-e", "HA_OPENWISP_STARTUP_TIMEOUT=1", "-e", "REDIS_PASS=dummy-redis-password",
                         "--entrypoint", "python", image, "/opt/ha-openwisp/initialize.py", check=False, timeout=10)
        assert missing.returncode == 1 and "internal PostgreSQL unavailable after 1s" in missing.stderr
        assert time.monotonic() - started < 8, "Dependency readiness was not bounded"
        startup = (root / "tests/fixtures/startup_failures.py").read_text()
        for label in ("Redis", "InfluxDB"):
            started = time.monotonic()
            missing = docker("run", "--rm", "-i", "--platform", "linux/amd64", "--network", "none",
                             "-e", "HA_OPENWISP_STARTUP_TIMEOUT=1", "--entrypoint", "python", image,
                             "-", label, input=startup, check=False, timeout=10)
            assert missing.returncode == 1 and f"internal {label} unavailable after 1s" in missing.stderr
            assert time.monotonic() - started < 8, f"{label} readiness was not bounded"
        result = docker("run", "--rm", "-i", "--platform", "linux/amd64", "--network", "none",
                        "--entrypoint", "python", image, "-", input=startup)
        print(result.stdout, end="", flush=True)
        print("PASS: unavailable PostgreSQL, Redis and InfluxDB have bounded readiness failures", flush=True)
        docker("volume", "create", volume)
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            create_tls_fixture(temporary)
            options = Path(temporary) / "options.json"
            values = {"dashboard_domain": "controller.example.org", "api_domain": "api.example.org",
                      "admin_username": "test-admin", "admin_email": "admin@example.org",
                      "admin_password": "disposable-admin-password", "django_secret_key": "x" * 64,
                      "runtime_mode": "production", "startup_timeout": 10}
            options.write_text(json.dumps(values))
            docker("run", "-d", "--platform", "linux/amd64", "--network", "none", "--name", name,
                   "-v", f"{volume}:/data", "-v", f"{options}:/data/options.json:ro",
                   "-v", f"{temporary}:/ssl:ro", image)
            wait_ready(name, 1)
            info = json.loads(docker("inspect", name).stdout)[0]
            assert not info["HostConfig"]["PortBindings"], "Unexpected published port"
            first = json.loads(docker("exec", "-i", name, "python", "-", "write", input=inside).stdout)
            print("PASS: empty-storage initialization, supplied administrator, PostGIS/metrics, ownership and local authenticated listeners", flush=True)
            docker("stop", "-t", "110", name, timeout=120)
            assert state(name)["ExitCode"] == 0, logs(name)
            docker("start", name)
            wait_ready(name, 2)
            second = json.loads(docker("exec", "-i", name, "python", "-", "read", input=inside).stdout)
            assert first == second, "Normal restart changed installation state"
            assert logs(name).count("initializing PostgreSQL cluster") == 1
            assert "reusing PostgreSQL cluster" in logs(name)
            print("PASS: second startup reuses accounts, bootstrap objects, keys, files, spatial records and metrics", flush=True)
            for count, service in enumerate(("postgres", "influxdb"), start=3):
                started = time.monotonic()
                # Fault injection uses only the root-only supervisor socket.
                docker("exec", name, "supervisorctl", "-c", "/opt/ha-openwisp/supervisord.conf",
                       "signal", "KILL", service)
                docker("wait", name, timeout=45)
                assert time.monotonic() - started < 45
                assert state(name)["ExitCode"] == 1, logs(name)
                assert f"{service} reported PROCESS_STATE_EXITED" in logs(name), logs(name)
                docker("start", name)
                wait_ready(name, count)
                recovered = json.loads(docker("exec", "-i", name, "python", "-", "read", input=inside).stdout)
                assert recovered == first, "Database recovery reset installation state"
                print(f"PASS: killed {service} causes bounded app failure; restart recovers all recorded state", flush=True)
            docker("stop", "-t", "110", name, timeout=120)
            assert state(name)["ExitCode"] == 0, logs(name)
            # Development fixture: fail the migration command, retaining the data
            # volume and the original role script for the subsequent recovery.
            original = Path(temporary) / "manage-original.py"
            fixture = Path(temporary) / "manage-failure.py"
            docker("cp", f"{name}:/opt/ha-openwisp/dashboard/manage.py", str(original))
            fixture.write_text("import os, sys\nprint(os.environ['DB_PASS'], file=sys.stderr)\nraise SystemExit(1)\n")
            docker("cp", str(fixture), f"{name}:/opt/ha-openwisp/dashboard/manage.py")
            docker("start", name)
            docker("wait", name, timeout=45)
            assert state(name)["ExitCode"] == 1, logs(name)
            assert "upstream migrations failed; existing data retained" in logs(name)
            assert logs(name).count("Listening on TCP address 127.0.0.1:8002") == 4, "Application started after migration failure"
            docker("cp", str(original), f"{name}:/opt/ha-openwisp/dashboard/manage.py")
            docker("start", name)
            wait_ready(name, 5)
            assert json.loads(docker("exec", "-i", name, "python", "-", "read", input=inside).stdout) == first
            print("PASS: migration-command failure blocks applications, hides upstream secrets and preserves state for restart", flush=True)
            credentials = json.loads(docker("exec", name, "cat", "/data/openwisp/secrets.json").stdout)
            output = logs(name)
            assert all(secret not in output for secret in (*credentials.values(), values["admin_password"],
                                                          values["django_secret_key"])), "Secret in logs"
            assert "killing '" not in output, "Shutdown forced termination"
            docker("stop", "-t", "110", name, timeout=120)
            assert state(name)["ExitCode"] == 0, logs(name)
            print("PASS: clean shutdown and secret-safe logs", flush=True)
    except Exception:
        output = docker("logs", name, check=False)
        print(output.stdout + output.stderr, flush=True)
        raise
    finally:
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)
        docker("image", "rm", image, check=False)


if __name__ == "__main__":
    main()
