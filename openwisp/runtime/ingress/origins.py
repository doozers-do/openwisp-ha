"""One explicit, bounded origin policy shared by startup and request validation."""
from ipaddress import IPv4Address, IPv6Address
import json
import re
from urllib.parse import urlsplit

MAX_ORIGINS = 16
ORIGIN_ERROR = 'ingress origins require exact HTTP(S) origins without paths, credentials or wildcards'


def canonical_origin(value):
    """Normalize case, IPv6 and default ports; reject ambiguous URL syntax."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 512
            or not re.fullmatch(r'https?://(?:[a-z0-9.-]+|\[[a-f0-9:.]+\])(?::[0-9]+)?',
                                value, re.IGNORECASE | re.ASCII)):
        raise ValueError(ORIGIN_ERROR)
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if not host or parsed.netloc.endswith(':') or port == 0:
            raise ValueError
        if ':' in host:
            host = '[' + IPv6Address(host).compressed + ']'
        elif re.fullmatch(r'[0-9.]+', host):
            host = str(IPv4Address(host))
        elif (len(host) > 253 or any(not re.fullmatch(
                r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                for label in host.split('.'))):
            raise ValueError
    except ValueError:
        raise ValueError(ORIGIN_ERROR) from None
    scheme = parsed.scheme.lower()
    suffix = f':{port}' if port is not None and port != {'http': 80, 'https': 443}[scheme] else ''
    return f'{scheme}://{host}{suffix}'


def validate_origins(values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_ORIGINS:
        raise ValueError(f'ingress_origins must contain 1 to {MAX_ORIGINS} origins')
    origins = [canonical_origin(value) for value in values]
    if len(set(origins)) != len(origins):
        raise ValueError('ingress origins must not contain duplicates')
    # Cookies have no port boundary. Sharing a hostname can also let HTTP
    # overwrite HTTPS cookies; require a distinct hostname for each origin.
    hosts = [urlsplit(origin).hostname for origin in origins]
    if len(set(hosts)) != len(hosts):
        raise ValueError('ingress origins must use distinct hostnames to isolate cookies')
    return origins


def configured_origins(values, *, enabled=True):
    if not isinstance(values, list):
        raise ValueError('ingress_origins must be a list')
    return validate_origins(values) if values or enabled else []


def environment_origins(environ):
    """The launcher exports only the validated JSON list."""
    return validate_origins(json.loads(environ.get('HA_OPENWISP_INGRESS_ORIGINS', '[]')))
