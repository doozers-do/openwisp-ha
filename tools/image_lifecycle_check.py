#!/usr/bin/env python3
"""Build and validate the single app image using disposable Docker state."""

import json
from pathlib import Path
import subprocess
import tempfile
import time
import uuid

from check_app_discovery import find_configs
from tls_fixture import create_tls_fixture


def docker(*arguments, timeout=60, check=True, input=None):
    result = subprocess.run(["docker", *arguments], input=input, text=True,
                            capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise AssertionError(f"docker {' '.join(arguments)} failed: {result.stdout}\n{result.stderr}")
    return result


def state(name):
    return json.loads(docker("inspect", name).stdout)[0]["State"]


def logs(name):
    result = docker("logs", name)
    return result.stdout + result.stderr


def wait_ready(name, count=1):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        output = logs(name)
        if output.count("Listening on TCP address 127.0.0.1:8002") >= count:
            return
        if not state(name)["Running"]:
            raise AssertionError(f"App exited during startup: {logs(name)}")
        time.sleep(0.25)
    raise AssertionError(f"Upstream ASGI startup timed out: {logs(name)}")


def main():
    root = Path(__file__).resolve().parents[1]
    configs = find_configs(root)
    assert configs == ["openwisp/config.yaml"], f"Expected exactly one discoverable app: {configs}"
    prefix = "ha-openwisp-" + uuid.uuid4().hex[:10]
    name, volume, image = prefix, prefix + "-data", prefix + ":test"
    inside = (root / "tests/fixtures/image_lifecycle.py").read_text()
    try:
        docker("build", "--platform", "linux/amd64", "-t", image, str(root / "openwisp"), timeout=600)
        docker("volume", "create", volume)
        started = time.monotonic()
        missing = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                         "-e", "REDIS_PASS=disposable-test-value", "-e", "HA_OPENWISP_STARTUP_TIMEOUT=1",
                         "--entrypoint", "python", image, "/opt/ha-openwisp/service.py", check=False, timeout=10)
        assert missing.returncode == 1 and "internal Redis unavailable after 1s" in missing.stderr, "Readiness failure was not reported"
        assert time.monotonic() - started < 8, "Readiness failure exceeded its bounded wait"
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            create_tls_fixture(temporary)
            options = Path(temporary) / "options.json"
            for bad, expected in (({}, "dashboard_domain"), ({"startup_timeout": 0}, "dashboard_domain"),
                                  ({"dashboard_domain": "controller.example.org",
                                    "api_domain": "api.example.org", "admin_username": "test-admin",
                                    "admin_email": "admin@example.org", "admin_password": "dummy-password",
                                    "django_secret_key": "x" * 64, "runtime_mode": "production",
                                    "startup_timeout": 0}, "startup_timeout")):
                options.write_text(json.dumps(bad))
                result = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                                "-v", f"{options}:/data/options.json:ro", image, check=False)
                assert result.returncode == 1 and expected in result.stderr, "Invalid options were accepted"
            valid_options = {
                "dashboard_domain": "controller.example.org", "api_domain": "api.example.org",
                "admin_username": "test-admin", "admin_email": "admin@example.org",
                "admin_password": "disposable-admin-password", "django_secret_key": "x" * 64,
                "runtime_mode": "production", "startup_timeout": 10,
            }
            options.write_text(json.dumps(valid_options))
            docker("run", "-d", "--platform", "linux/amd64", "--network", "none", "--name", name,
                   "-v", f"{volume}:/data", "-v", f"{options}:/data/options.json:ro",
                   "-v", f"{temporary}:/ssl:ro", image)
            wait_ready(name)
            info = json.loads(docker("inspect", name).stdout)[0]
            assert not info["HostConfig"]["PortBindings"], "Foundation publishes a host port"
            first = json.loads(docker("exec", "-i", name, "python", "-", "write", input=inside).stdout)
            docker("cp", str(root / "tests/fixtures/dependency_compatibility.py"), f"{name}:/tmp/dependency_compatibility.py")
            dependencies = docker("exec", name, "python", "/tmp/dependency_compatibility.py", timeout=600)
            print(dependencies.stdout, end="", flush=True)
            docker("stop", "-t", "90", name, timeout=100)
            assert state(name)["ExitCode"] == 0, logs(name)
            assert "all internal services stopped cleanly" in logs(name), logs(name)
            docker("start", name)
            wait_ready(name, count=2)
            second = json.loads(docker("exec", "-i", name, "python", "-", "read", input=inside).stdout)
            assert first == second, "Keys or persistent cache state changed across restart"
            # Required child failure must shut down the app and preserve its state.
            docker("exec", name, "supervisorctl", "-c", "/opt/ha-openwisp/supervisord.conf", "signal", "KILL", "websocket")
            docker("wait", name, timeout=20)
            assert state(name)["ExitCode"] == 1, logs(name)
            docker("start", name)
            wait_ready(name, count=3)
            recovered = json.loads(docker("exec", "-i", name, "python", "-", "read", input=inside).stdout)
            assert recovered == first, "Recovery reset persistent credentials or cache"
            docker("exec", name, "supervisorctl", "-c", "/opt/ha-openwisp/supervisord.conf", "signal", "KILL", "redis")
            docker("wait", name, timeout=20)
            assert state(name)["ExitCode"] == 1, logs(name)
            docker("start", name)
            wait_ready(name, count=4)
            recovered = json.loads(docker("exec", "-i", name, "python", "-", "read", input=inside).stdout)
            assert recovered == first, "Redis recovery reset persistent state"
            all_logs = logs(name)
            credentials = json.loads(docker("exec", name, "cat", "/data/openwisp/secrets.json").stdout)
            assert all(secret not in all_logs for secret in credentials.values()), "A generated secret appeared in logs"
            assert all(secret not in all_logs for secret in
                       (valid_options["admin_password"], valid_options["django_secret_key"])), "A supplied secret appeared in logs"
            docker("stop", "-t", "90", name, timeout=100)
            assert state(name)["ExitCode"] == 0, logs(name)
            readonly = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                              "-v", f"{volume}:/data:ro", "-v", f"{options}:/data/options.json:ro",
                              image, check=False)
            assert readonly.returncode == 1 and "/data/openwisp" in readonly.stderr, "Read-only state was accepted"
            corrupt = "from pathlib import Path; Path('/data/openwisp/secrets.json').write_text('invalid-json')"
            docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                   "-v", f"{volume}:/data", "--entrypoint", "python", image, "-c", corrupt)
            damaged = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                             "-v", f"{volume}:/data", "-v", f"{options}:/data/options.json:ro",
                             image, check=False)
            assert damaged.returncode == 1 and "secrets.json is unreadable or damaged" in damaged.stderr, "Damaged credentials were reset"
            retained = docker("run", "--rm", "--platform", "linux/amd64", "--network", "none",
                              "-v", f"{volume}:/data", "--entrypoint", "cat", image, "/data/openwisp/secrets.json")
            assert retained.stdout == "invalid-json", "Damaged credentials were overwritten"
            print("PASS: pinned PostgreSQL/PostGIS, InfluxDB, Nginx/Brotli and Python builds; "
                  "exactly one app; real upstream ASGI/Redis; Django cache and Channels; "
                  "authenticated loopback services; restart persistence; clean shutdown; "
                  "required-process failure/recovery; no generated secrets in logs")
    finally:
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)
        docker("image", "rm", image, check=False)


if __name__ == "__main__":
    main()
