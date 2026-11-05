#!/usr/bin/env python3
"""Exercise the app options contract without printing supplied credentials."""

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("openwisp_settings", ROOT / "openwisp/runtime/settings.py")
settings = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = settings
spec.loader.exec_module(settings)


def main():
    secret = "dummy-django-secret-" + "x" * 48
    password = "dummy-admin-password"
    raw = {
        "dashboard_domain": "controller.example.org",
        "api_domain": "api.example.org",
        "admin_username": "site-admin",
        "admin_email": "admin@example.org",
        "admin_password": password,
        "django_secret_key": secret,
        "runtime_mode": "production",
        "startup_timeout": 60,
    }
    options = settings.validate_options(raw)
    assert options["celery_concurrency"] == 2
    credentials = {"redis": "r" * 64, "database": "d" * 64, "influxdb": "i" * 64}
    translated = settings.upstream_environment({"SUPERVISOR_TOKEN": "private"}, options, credentials)
    assert translated["HA_OPENWISP_CELERY_CONCURRENCY"] == "2"
    assert translated["DASHBOARD_DOMAIN"] == "controller.example.org"
    assert translated["API_DOMAIN"] == "api.example.org"
    assert translated["DJANGO_SECRET_KEY"] == secret
    assert translated["DEV_MODE"] == translated["DEBUG_MODE"] == "False"
    assert translated["HA_OPENWISP_ADMIN_USERNAME"] == "site-admin"
    assert translated["HA_OPENWISP_ADMIN_PASSWORD"] == password
    assert translated["DB_HOST"] == translated["REDIS_HOST"] == "127.0.0.1"
    assert translated["USE_OPENWISP_RADIUS"] == translated["USE_OPENWISP_FIRMWARE"] == "False"
    assert translated["USE_OPENWISP_TOPOLOGY"] == "True"
    assert translated["SSL_CERT_MODE"] == "True"
    assert translated["NGINX_SSL_PORT"] == "443"
    assert options["certfile"] == "fullchain.pem" and options["keyfile"] == "privkey.pem"
    alternate = settings.validate_options({**raw, "https_port": 8443})
    assert settings.upstream_environment({}, alternate, credentials)["NGINX_SSL_PORT"] == "8443"
    local = settings.validate_options({**raw, "dashboard_domain": "homeassistant.local", "api_domain": "homeassistant.local"})
    assert local["dashboard_domain"] == local["api_domain"] == "homeassistant.local"
    assert "SUPERVISOR_TOKEN" not in translated

    for slots in (1, 2, 3, 4):
        configured = settings.validate_options({**raw, "celery_concurrency": slots})
        env = settings.upstream_environment({}, configured, credentials)
        assert env["HA_OPENWISP_CELERY_CONCURRENCY"] == str(slots)

    supervisor_config = (ROOT / "openwisp/runtime/supervisord.conf").read_text()
    assert "[program:celery]" in supervisor_config
    assert "--concurrency=%(ENV_HA_OPENWISP_CELERY_CONCURRENCY)s" in supervisor_config
    assert "--queues=celery,network,monitoring,monitoring_checks" in supervisor_config
    assert all(f"[program:{name}]" not in supervisor_config
               for name in ("network", "monitoring", "monitoring_checks"))

    for invalid, expected in (
        ({**raw, "certfile": "../secret"}, "certfile"),
        ({**raw, "keyfile": "/data/secret"}, "keyfile"),
        ({**raw, "https_port": 0}, "https_port"),
        ({**raw, "https_port": True}, "https_port"),
        ({**raw, "celery_concurrency": 0}, "celery_concurrency"),
        ({**raw, "celery_concurrency": 5}, "celery_concurrency"),
        ({**raw, "celery_concurrency": True}, "celery_concurrency"),
        ({**raw, "celery_concurrency": False}, "celery_concurrency"),
        ({**raw, "celery_concurrency": 2.0}, "celery_concurrency"),
        ({**raw, "celery_concurrency": "2"}, "celery_concurrency"),
        ({**raw, "admin_password": ""}, "admin_password"),
        ({**raw, "dashboard_domain": "https://bad.example"}, "dashboard_domain"),
        ({**raw, "runtime_mode": "debug"}, "runtime_mode"),
        ({**raw, "runtime_mode": []}, "runtime_mode"),
        ({key: value for key, value in raw.items() if key != "django_secret_key"}, "django_secret_key"),
    ):
        try:
            settings.validate_options(invalid)
        except settings.OptionError as error:
            assert expected in str(error)
            assert password not in str(error) and secret not in str(error)
        else:
            raise AssertionError(f"invalid {expected} was accepted")

    dev = settings.validate_options({**raw, "runtime_mode": "development"})
    translated_dev = settings.upstream_environment({}, dev, credentials)
    assert translated_dev["DEV_MODE"] == translated_dev["DEBUG_MODE"] == "True"
    print("PASS: required/invalid options, Celery sizing defaults/range/types, Supervisor propagation, upstream translation, and secret-safe errors")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print(f"FAIL: {error or 'app settings contract'}", file=sys.stderr)
        sys.exit(1)
