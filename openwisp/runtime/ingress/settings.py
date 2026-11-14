"""Settings for the trusted ingress process only."""
from openwisp.settings import *  # noqa: F403
import os
from urllib.parse import urlsplit
from .origins import environment_origins

INGRESS_ORIGINS = environment_origins(os.environ)
ALLOWED_HOSTS = ['[' + urlsplit(origin).hostname + ']' if ':' in urlsplit(origin).hostname
                 else urlsplit(origin).hostname for origin in INGRESS_ORIGINS]
ROOT_URLCONF = 'ingress.urls'
SESSION_COOKIE_NAME = 'openwisp_ingress_session'
CSRF_COOKIE_NAME = 'openwisp_ingress_csrf'
SESSION_COOKIE_DOMAIN = CSRF_COOKIE_DOMAIN = None
# ResponsePolicy sets Secure from the validated request scheme, after Django's
# cookie middleware. Distinct configured hostnames prevent HTTP/HTTPS collisions.
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = False
SESSION_COOKIE_SAMESITE = CSRF_COOKIE_SAMESITE = 'Lax'
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = None
USE_X_FORWARDED_HOST = USE_X_FORWARDED_PORT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_PRELOAD = False
X_FRAME_OPTIONS = 'SAMEORIGIN'
CORS_ALLOWED_ORIGINS = []
CSRF_TRUSTED_ORIGINS = []
OPENWISP_NOTIFICATIONS_HOST = None
OPENWISP_CONTROLLER_API_HOST = None
OPENWISP_MONITORING_API_BASEURL = None
OPENWISP_NETWORK_TOPOLOGY_API_BASEURL = None
LOCI_FLOORPLAN_STORAGE = 'ingress.storage.FloorPlan'
MIDDLEWARE = ['ingress.middleware.ResponsePolicy', *MIDDLEWARE]
MIDDLEWARE.insert(MIDDLEWARE.index('django.contrib.auth.middleware.AuthenticationMiddleware') + 1,
                  'ingress.middleware.Identity')
STORAGES = {
    'default': {'BACKEND': 'ingress.storage.Media'},
    'staticfiles': {'BACKEND': 'ingress.storage.Static'},
}
processors = TEMPLATES[0]['OPTIONS']['context_processors']
upstream_menu = 'openwisp_utils.admin_theme.context_processor.menu_groups'
if processors.count(upstream_menu) != 1:
    raise RuntimeError('Expected exactly one upstream admin menu context processor')
processors[processors.index(upstream_menu)] = 'ingress.menu.menu_groups'
processors.append('ingress.middleware.template_context')
