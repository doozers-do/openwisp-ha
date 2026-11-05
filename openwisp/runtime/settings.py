"""Validate Supervisor options and translate them to upstream OpenWISP settings."""

import re
import json
from urllib.parse import urlsplit


class OptionError(ValueError):
    pass


def _domain(name, value):
    if not isinstance(value, str) or not value or len(value) > 253:
        raise OptionError(f"{name} must be a DNS name")
    try:
        labels = value.rstrip(".").split(".")
        if any(not label or len(label) > 63 or
               not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label)
               for label in labels):
            raise ValueError
    except (AttributeError, ValueError):
        raise OptionError(f"{name} must be a DNS name") from None
    return value.rstrip(".").lower()


def validate_options(value):
    if not isinstance(value, dict):
        raise OptionError("/data/options.json must contain an object")
    direct = value.get('direct_https', True)  # Saved pre-ingress installations.
    ingress = value.get('ingress_enabled', False)
    if type(direct) is not bool or type(ingress) is not bool or not (direct or ingress):
        raise OptionError('enable direct_https or ingress_enabled with boolean values')
    dashboard = _domain("dashboard_domain", value.get("dashboard_domain") or (None if direct else 'openwisp.invalid'))
    api = _domain("api_domain", value.get("api_domain") or (None if direct else 'openwisp.invalid'))
    ingress_origin = value.get('ingress_origin', '')
    if ingress:
        try:
            parsed = urlsplit(ingress_origin)
            if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                    or parsed.username or parsed.password or parsed.path
                    or parsed.query or parsed.fragment or parsed.port == 0
                    or not re.fullmatch(r'https?://[A-Za-z0-9.:-]+', ingress_origin)):
                raise ValueError
        except (ValueError, TypeError):
            raise OptionError('ingress_origin must be the exact browser-facing HTTP(S) origin without a path') from None
    users = value.get('ingress_users', [])
    if not isinstance(users, list):
        raise OptionError('ingress_users must be a list of explicit user mappings')
    mapping = {}
    for entry in users:
        if (not isinstance(entry, dict) or set(entry) != {'ha_user_id', 'openwisp_username'}
                or not isinstance(entry['ha_user_id'], str)
                or not re.fullmatch(r'[a-f0-9]{32}', entry['ha_user_id'])
                or not isinstance(entry['openwisp_username'], str)
                or not re.fullmatch(r'[A-Za-z0-9_.@+-]{1,150}', entry['openwisp_username'])
                or entry['ha_user_id'] in mapping):
            raise OptionError('ingress_users requires unique Home Assistant IDs and existing OpenWISP usernames')
        mapping[entry['ha_user_id']] = entry['openwisp_username']
    username = value.get("admin_username")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.@+-]{1,150}", username):
        raise OptionError("admin_username must be 1 to 150 letters, digits, or ._@+-")
    email = value.get("admin_email")
    if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise OptionError("admin_email must be a valid email address")
    password = value.get("admin_password")
    if not isinstance(password, str) or len(password) < 12 or "\n" in password or "\r" in password:
        raise OptionError("admin_password must contain at least 12 characters")
    django_key = value.get("django_secret_key")
    if not isinstance(django_key, str) or len(django_key) < 50 or "\n" in django_key or "\r" in django_key:
        raise OptionError("django_secret_key must contain at least 50 characters")
    mode = value.get("runtime_mode")
    if not isinstance(mode, str) or mode not in {"production", "development"}:
        raise OptionError("runtime_mode must be production or development")
    timeout = value.get("startup_timeout")
    if type(timeout) is not int or not 1 <= timeout <= 300:
        raise OptionError("startup_timeout must be an integer from 1 to 300")
    celery_concurrency = value.get("celery_concurrency", 2)
    if type(celery_concurrency) is not int or not 1 <= celery_concurrency <= 4:
        raise OptionError("celery_concurrency must be an integer from 1 to 4")
    tls_files = {}
    for name, default in (("certfile", "fullchain.pem"), ("keyfile", "privkey.pem")):
        filename = value.get(name, default)
        if not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,254}", filename):
            raise OptionError(f"{name} must be a filename in Home Assistant's ssl directory")
        tls_files[name] = filename
    https_port = value.get("https_port", 443)
    if type(https_port) is not int or not 1 <= https_port <= 65535:
        raise OptionError("https_port must be an integer from 1 to 65535")
    canonical = f'https://{api}' + (f':{https_port}' if https_port != 443 else '')
    external_url = value.get('external_url', '') or canonical
    if external_url != canonical:
        raise OptionError('external_url must match https://api_domain and https_port (omit port 443)')
    return {
        'direct_https': direct, 'ingress_enabled': ingress,
        'ingress_origin': ingress_origin, 'ingress_users': mapping,
        'external_url': external_url,
        **tls_files,
        "https_port": https_port,
        "dashboard_domain": dashboard,
        "api_domain": api,
        "admin_username": username,
        "admin_email": email,
        "admin_password": password,
        "django_secret_key": django_key,
        "runtime_mode": mode,
        "startup_timeout": timeout,
        "celery_concurrency": celery_concurrency,
    }


def upstream_environment(base, options, credentials):
    """Return the pinned upstream environment plus package-owned internal addresses."""
    development = options["runtime_mode"] == "development"
    result = base.copy()
    result.update({
        'HA_OPENWISP_DIRECT_HTTPS': str(options['direct_https']),
        'HA_OPENWISP_INGRESS_ENABLED': str(options['ingress_enabled']),
        'HA_OPENWISP_INGRESS_ORIGIN': options['ingress_origin'],
        'HA_OPENWISP_INGRESS_USERS': json.dumps(options['ingress_users']),
        'HA_OPENWISP_EXTERNAL_URL': options['external_url'],
        'OPENWISP_CONTROLLER_API_HOST': options['external_url'],
        'OPENWISP_MONITORING_API_BASEURL': options['external_url'],
        'OPENWISP_NETWORK_TOPOLOGY_API_BASEURL': options['external_url'],
        "MODULE_NAME": "websocket", "CONTAINER_PORT": "8002",
        "DJANGO_WEBSOCKET_HOST": "127.0.0.1",
        "DASHBOARD_DOMAIN": options["dashboard_domain"],
        "API_DOMAIN": options["api_domain"],
        "DASHBOARD_APP_SERVICE": "127.0.0.1",
        "DASHBOARD_INTERNAL": "127.0.0.1", "API_INTERNAL": "127.0.0.1",
        "DJANGO_ALLOWED_HOSTS": f"{options['dashboard_domain']},{options['api_domain']},127.0.0.1,localhost",
        "DJANGO_SECRET_KEY": options["django_secret_key"],
        # Create the first administrator before upstream's default-admin fallback.
        "HA_OPENWISP_ADMIN_USERNAME": options["admin_username"],
        "HA_OPENWISP_ADMIN_EMAIL": options["admin_email"],
        "HA_OPENWISP_ADMIN_PASSWORD": options["admin_password"],
        "REDIS_HOST": "127.0.0.1", "REDIS_PORT": "6379",
        "REDIS_PASS": credentials["redis"], "DB_HOST": "127.0.0.1",
        "DB_PASS": credentials["database"], "DB_PORT": "5432",
        "DB_USER": "admin", "DB_NAME": "openwisp_db",
        "DB_ENGINE": "django.contrib.gis.db.backends.postgis", "DB_SSLMODE": "disable",
        "DB_OPTIONS": "{}", "INFLUXDB_HOST": "127.0.0.1", "INFLUXDB_PORT": "8086",
        "INFLUXDB_USER": "admin", "INFLUXDB_NAME": "openwisp",
        "INFLUXDB_PASS": credentials["influxdb"],
        "SSH_PRIVATE_KEY_PATH": "/data/openwisp/ssh/id_ed25519",
        "SSH_PUBLIC_KEY_PATH": "/data/openwisp/ssh/id_ed25519.pub",
        "DEV_MODE": str(development), "DEBUG_MODE": str(development),
        "SSL_CERT_MODE": "True", "NGINX_SSL_PORT": str(options["https_port"]), "DJANGO_LOG_LEVEL": "DEBUG" if development else "INFO",
        "USE_OPENWISP_RADIUS": "False", "USE_OPENWISP_TOPOLOGY": "True",
        "USE_OPENWISP_FIRMWARE": "False", "USE_OPENWISP_MONITORING": "True",
        "USE_OPENWISP_CELERY_NETWORK": "True",
        "USE_OPENWISP_CELERY_MONITORING": "True",
        "USE_OPENWISP_CELERY_FIRMWARE": "False",
        "VPN_DOMAIN": "", "METRIC_COLLECTION": "False",
        "EMAIL_BACKEND": "django.core.mail.backends.dummy.EmailBackend",
        "HA_OPENWISP_STARTUP_TIMEOUT": str(options["startup_timeout"]),
        "HA_OPENWISP_CELERY_CONCURRENCY": str(options["celery_concurrency"]),
    })
    result.pop("SUPERVISOR_TOKEN", None)
    result.pop("HASSIO_TOKEN", None)
    return result
