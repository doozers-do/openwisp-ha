"""Settings for the trusted ingress process only."""
from openwisp.settings import *  # noqa: F403
import os
from urllib.parse import urlsplit

INGRESS_ORIGIN = os.environ['HA_OPENWISP_INGRESS_ORIGIN']
ALLOWED_HOSTS = [urlsplit(INGRESS_ORIGIN).hostname]
ROOT_URLCONF = 'ingress.urls'
SESSION_COOKIE_NAME = 'openwisp_ingress_session'
CSRF_COOKIE_NAME = 'openwisp_ingress_csrf'
SESSION_COOKIE_DOMAIN = CSRF_COOKIE_DOMAIN = None
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = INGRESS_ORIGIN.startswith('https:')
SECURE_SSL_REDIRECT = False
SECURE_PROXY_SSL_HEADER = None
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_PRELOAD = False
X_FRAME_OPTIONS = 'SAMEORIGIN'
CORS_ALLOWED_ORIGINS = []
CSRF_TRUSTED_ORIGINS = [INGRESS_ORIGIN]
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
TEMPLATES[0]['OPTIONS']['context_processors'].append('ingress.middleware.template_context')
