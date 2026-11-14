"""Use host-only login cookies when dashboard and API share one hostname."""
import os as _os

SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SESSION_COOKIE_NAME = 'openwisp_direct_session'
CSRF_COOKIE_NAME = 'openwisp_direct_csrf'
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE = True
__all__ = ['SECURE_HSTS_SECONDS', 'SECURE_HSTS_INCLUDE_SUBDOMAINS', 'SECURE_HSTS_PRELOAD']
__all__ += ['SESSION_COOKIE_NAME', 'CSRF_COOKIE_NAME', 'SESSION_COOKIE_SECURE', 'CSRF_COOKIE_SECURE']
if _os.environ['DASHBOARD_DOMAIN'] == _os.environ['API_DOMAIN']:
    # Upstream derives a registrable cookie domain from the public suffix list.
    # Names such as homeassistant.local have none; a host-only cookie is valid.
    SESSION_COOKIE_DOMAIN = None
    CSRF_COOKIE_DOMAIN = None
    __all__ += ['SESSION_COOKIE_DOMAIN', 'CSRF_COOKIE_DOMAIN']
