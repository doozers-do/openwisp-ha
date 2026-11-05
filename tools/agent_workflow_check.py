#!/usr/bin/env python3
"""Build the app and run disposable Config/Monitoring agent protocol checks."""
import json
from pathlib import Path
import secrets
import tempfile
import time
import uuid

from image_lifecycle_check import docker, logs, state
from tls_fixture import create_tls_fixture

ROOT = Path(__file__).resolve().parents[1]
ROLES = {"dashboard", "api", "websocket", "celery", "scheduler", "nginx"}


def ready(name):
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        assert state(name)["Running"], logs(name)
        status = docker("exec", name, "supervisorctl", "-c", "/opt/ha-openwisp/supervisord.conf",
                        "status", check=False).stdout
        running = {line.split()[0] for line in status.splitlines() if "RUNNING" in line}
        if ROLES.issubset(running):
            return
        time.sleep(0.5)
    raise AssertionError("OpenWISP roles did not become ready:\n" + logs(name))


def agent_request(image, network, fixture, ca_file, action):
    result = docker("run", "--rm", "--platform", "linux/amd64", "--network", network,
                    "-v", f"{fixture}:/fixture:ro",
                    "-v", f"{ROOT / 'tests/fixtures/agent_client.py'}:/tmp/agent_client.py:ro",
                    "--entrypoint", "python", image,
                    "/tmp/agent_client.py", "/fixture/device.json", f"/fixture/{ca_file}", action,
                    timeout=90)
    print(result.stdout, end="", flush=True)


def main():
    prefix = "ha-openwisp-agent-" + uuid.uuid4().hex[:10]
    image, name = prefix + ":test", prefix
    network, volume = prefix + "-net", prefix + "-data"
    try:
        docker("build", "--platform", "linux/amd64", "-t", image,
               str(ROOT / "openwisp"), timeout=600)
        docker("network", "create", network)
        docker("volume", "create", volume)
        with tempfile.TemporaryDirectory(prefix=prefix) as temporary:
            fixture = Path(temporary)
            create_tls_fixture(fixture)
            options = {
                "direct_https": True,
                "dashboard_domain": "controller.example.org",
                "api_domain": "api.example.org",
                "admin_username": "agent-smoke-admin",
                "admin_email": "agent-smoke@example.org",
                "admin_password": secrets.token_urlsafe(24),
                "django_secret_key": secrets.token_urlsafe(48),
                "runtime_mode": "production",
                "startup_timeout": 10,
            }
            (fixture / "options.json").write_text(json.dumps(options))
            docker("run", "-d", "--platform", "linux/amd64", "--network", network,
                   "--network-alias", options["api_domain"], "--network-alias", options["dashboard_domain"],
                   "--name", name, "-v", f"{volume}:/data",
                   "-v", f"{fixture}:/ssl:ro",
                   "-v", f"{fixture / 'options.json'}:/data/options.json:ro",
                   "-v", f"{ROOT / 'tests/fixtures/agent_data.py'}:/tmp/agent_data.py:ro", image)
            ready(name)
            setup = docker("exec", name, "python", "/tmp/agent_data.py", "setup")
            device = json.loads(setup.stdout)
            device_file = fixture / "device.json"
            device_file.write_text(json.dumps(device))
            agent_request(image, network, fixture, "fullchain.pem", "config-before")

            docker("exec", name, "python", "/tmp/agent_data.py", "update",
                   device["template_id"], device["after"])
            agent_request(image, network, fixture, "fullchain.pem", "config-after")
            agent_request(image, network, fixture, "fullchain.pem", "monitoring")

            docker("exec", name, "python", "/tmp/agent_data.py", "cleanup", device["organization_id"])
            docker("stop", "-t", "110", name, timeout=120)
            assert state(name)["ExitCode"] == 0, logs(name)
            print("PASS: disposable OpenWISP identity/template removed; app stopped cleanly", flush=True)
    except Exception:
        if name:
            output = docker("logs", "--tail", "100", name, check=False)
            print(output.stdout + output.stderr, flush=True)
        raise
    finally:
        docker("rm", "-f", name, check=False)
        docker("volume", "rm", volume, check=False)
        docker("network", "rm", network, check=False)
        docker("image", "rm", image, check=False)


if __name__ == "__main__":
    main()
