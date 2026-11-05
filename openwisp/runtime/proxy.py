"""Adapt the pinned Nginx routes for Debian and HTTPS served inside the Home Assistant app."""

from pathlib import Path
from string import Template
import ssl
import subprocess

UPSTREAM = Path('/opt/ha-openwisp/upstream/nginx')
TEMPLATES = Path(__file__).parent / 'nginx'
RUNTIME = Path('/run/ha-openwisp')


class TLSConfigurationError(ValueError):
    pass


def validate_certificate(options):
    paths = {}
    for setting in ('certfile', 'keyfile'):
        path = Path('/ssl') / options[setting]
        if not path.resolve().is_relative_to(Path('/ssl')) or not path.is_file():
            raise TLSConfigurationError(f"{setting}: file must exist in Home Assistant's ssl directory")
        paths[setting] = path
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(paths['certfile'], paths['keyfile'], password=lambda: '')
        for args in (('-checkend', '0'), ('-checkhost', options['dashboard_domain']),
                     ('-checkhost', options['api_domain'])):
            result = subprocess.run(['openssl', 'x509', '-in', str(paths['certfile']), '-noout', *args],
                                    capture_output=True, timeout=5)
            if result.returncode:
                raise TLSConfigurationError('certfile must be unexpired and cover dashboard_domain and api_domain')
    except (OSError, ssl.SSLError, subprocess.TimeoutExpired):
        raise TLSConfigurationError('certfile/keyfile must be a readable PEM certificate chain and matching unencrypted private key') from None


def configure_proxy(options):
    direct = options.get('direct_https', True)
    if direct:
        validate_certificate(options)
    routes = []
    roles = [(options['dashboard_domain'], 8000)]
    shared_host = options['dashboard_domain'] == options['api_domain']
    if not shared_host:
        roles.append((options['api_domain'], 8001))
    if not direct:
        roles = []
    for domain, port in roles:
        extra_routes = 'location /private/ { return 404; }'
        if shared_host:
            extra_routes += '''
    location ~ ^/(api|controller|monitoring)/ {
        include /etc/nginx/uwsgi_params;
        uwsgi_pass 127.0.0.1:8001;
        uwsgi_param HTTP_X_FORWARDED_PROTO https;
        uwsgi_param HTTPS on;
        uwsgi_param REMOTE_ADDR $remote_addr;
    }
'''

        security = (TEMPLATES / 'security.conf.template').read_text()
        origins = ' '.join(f"https://{options[name]}:{options['https_port']}"
                           for name in ('dashboard_domain', 'api_domain'))
        security_path = RUNTIME / f'security-{port}.conf'
        security_path.write_text(Template(security).substitute(
            DOMAIN=domain, ORIGINS=origins, DOLLAR='$'))
        route = Template((UPSTREAM / 'openwisp.ssl.template.conf').read_text()).substitute(
            DOMAIN=domain, DOLLAR='$', NGINX_IP6_STRING='listen [::]:443 ssl;', NGINX_HTTP2='',
            NGINX_GZIP_SWITCH='on', NGINX_GZIP_LEVEL='6', NGINX_GZIP_PROXIED='any',
            NGINX_GZIP_MIN_LENGTH='256', NGINX_GZIP_TYPES='text/css application/javascript application/json',
            NGINX_SSL_CONFIG=extra_routes,
            NGINX_CLIENT_BODY_SIZE='30', NGINX_ADMIN_ALLOW_NETWORK='all',
            NGINX_SECURITY_HEADERS_FILE=str(security_path),
            WEBSOCKET_APP_SERVICE='127.0.0.1', WEBSOCKET_APP_PORT='8002',
            NGINX_BROTLI_SWITCH='on', APP_SERVICE='127.0.0.1', APP_PORT=str(port),
        )
        route = route.replace(f'/etc/letsencrypt/live/{domain}/fullchain.pem',
                              str(Path('/ssl') / options['certfile']))
        route = route.replace(f'/etc/letsencrypt/live/{domain}/privkey.pem',
                              str(Path('/ssl') / options['keyfile']))
        route = route.replace('/opt/openwisp/public', '/data/openwisp')
        # Only explicit static/media locations may read the persistent tree.
        route = route.replace('root /data/openwisp/;', 'root /var/empty;')
        route = route.replace('include uwsgi_params;', 'include /etc/nginx/uwsgi_params;')
        route = route.replace('HTTP_X_FORWARDED_PROTO https;',
                              'HTTP_X_FORWARDED_PROTO https;\n        uwsgi_param HTTPS on;')
        route = route.replace('proxy_redirect off;', 'proxy_set_header X-Forwarded-Proto https;\n        proxy_redirect off;')
        # Upstream logs full request URLs, which may carry agent keys.
        route = route.replace('access_log /dev/stdout main;', 'access_log off;')
        route = route.replace('error_log /dev/stdout error;', 'error_log /dev/stderr crit;')
        # Identity and ingress context are meaningful only on the private listener.
        for header in ('X-Remote-User-Id', 'X-Remote-User-Name', 'X-Remote-User-Display-Name',
                       'X-Remote-User-Groups', 'X-Ingress-Path', 'X-Openwisp-Peer',
                       'X-Forwarded-Host', 'X-Forwarded-Port'):
            route = route.replace('include /etc/nginx/uwsgi_params;',
                f'uwsgi_param HTTP_{header.upper().replace("-", "_")} "";\n        include /etc/nginx/uwsgi_params;')
            route = route.replace('proxy_redirect off;', f'proxy_set_header {header} "";\n        proxy_redirect off;')
        routes.append(route)
    # Reject unknown hosts instead of upstream's unconditional /status response.
    config = '''load_module /usr/lib/nginx/modules/ngx_http_brotli_static_module.so;
user www-data;
worker_processes 1;
pid /run/ha-openwisp/nginx.pid;
error_log /dev/stderr crit;
events { worker_connections 256; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    server_names_hash_bucket_size 256;
    server_tokens off;
    access_log off;
    sendfile on;
    # Never pass application transport policy to a shared host. Only the known
    # affected hostname receives a deletion; other installations emit no HSTS.
    uwsgi_hide_header Strict-Transport-Security;
    proxy_hide_header Strict-Transport-Security;
    map $host $openwisp_hsts_recovery {
        default "";
        homeassistant.local "max-age=0";
    }
'''
    if direct:
        config += '''    server {
        listen 443 ssl default_server;
        listen [::]:443 ssl default_server;
        ssl_reject_handshake on;
    }
'''
    if options.get('ingress_enabled'):
        routes.append((TEMPLATES / 'ingress.conf').read_text())
    (RUNTIME / 'nginx.conf').write_text(config + '\n'.join(routes) + '\n}\n')
