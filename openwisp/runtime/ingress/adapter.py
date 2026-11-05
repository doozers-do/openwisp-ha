"""Ingress trust and prefix adapters for isolated application entry points.

The server must pass the actual transport peer (never X-Forwarded-For). An
application-specific resolver must return an existing, active OpenWISP account.
This module never creates users, changes permissions, or trusts group headers.
"""
from dataclasses import dataclass
import re
from urllib.parse import urlsplit

SUPERVISOR = '172.30.32.2'
PREFIX = re.compile(r'/api/hassio_ingress/[A-Za-z0-9_-]{1,256}')
USER_ID = re.compile(r'[a-f0-9]{32}')


class Rejected(ValueError):
    """Untrusted or malformed ingress request, without echoing supplied values."""


@dataclass(frozen=True)
class Context:
    prefix: str
    scheme: str
    host: str
    user_id: str


def context(peer, headers, allowed_origins):
    if peer != SUPERVISOR:
        raise Rejected('untrusted peer')
    prefix = headers.get('x-ingress-path', '')
    scheme = headers.get('x-forwarded-proto', '')
    host = headers.get('host', '')
    user_id = headers.get('x-remote-user-id', '')
    if not PREFIX.fullmatch(prefix) or not USER_ID.fullmatch(user_id):
        raise Rejected('invalid ingress context')
    # Explicit browser-facing origins keep proxy headers from widening allowed
    # hosts or CSRF origins. No comma lists, userinfo, control bytes, or paths.
    origin = f'{scheme}://{host}'
    parsed = urlsplit(origin)
    if (scheme not in ('http', 'https') or origin not in allowed_origins
            or any(ord(char) < 33 or ord(char) > 126 for char in host)
            or parsed.username or parsed.password or parsed.path
            or parsed.query or parsed.fragment or not parsed.hostname):
        raise Rejected('invalid browser origin')
    return Context(prefix, scheme, host, user_id)


def account(ctx, mapping, resolve):
    username = mapping.get(ctx.user_id)
    user = resolve(username) if username else None
    if user is None or not user.is_active:
        raise Rejected('identity is not authorized')
    return user


class WSGI:
    def __init__(self, app, *, origins, mapping, resolve):
        self.app, self.origins, self.mapping, self.resolve = app, origins, mapping, resolve

    def __call__(self, environ, start_response):
        try:
            ctx = context(environ.get('REMOTE_ADDR'), {
                key[5:].replace('_', '-').lower(): value
                for key, value in environ.items() if key.startswith('HTTP_')
            }, self.origins)
            user = account(ctx, self.mapping, self.resolve)
            path = environ.get('PATH_INFO', '')
            if not path.startswith('/') or path.startswith('//'):
                raise Rejected('invalid application path')
        except (Rejected, ValueError):
            start_response('403 Forbidden', [('Content-Type', 'text/plain'),
                                             ('Content-Length', '9')])
            return [b'Forbidden']
        adapted = environ.copy()
        adapted.update(SCRIPT_NAME=ctx.prefix, PATH_INFO=path,
                       **{'wsgi.url_scheme': ctx.scheme, 'HTTP_HOST': ctx.host,
                          'openwisp.ingress': ctx, 'openwisp.user': user})
        # The app must consume the validated context, not these original headers.
        for key in tuple(adapted):
            if key.startswith(('HTTP_X_REMOTE_', 'HTTP_X_FORWARDED_', 'HTTP_X_INGRESS_')):
                del adapted[key]
        return self.app(adapted, start_response)


class ASGI:
    def __init__(self, app, *, origins, mapping, resolve):
        # resolve is async: Django ORM lookups must run through database_sync_to_async.
        self.app, self.origins, self.mapping, self.resolve = app, origins, mapping, resolve

    async def __call__(self, scope, receive, send):
        try:
            if scope['type'] not in ('http', 'websocket'):
                raise Rejected('unsupported scope')
            headers = {}
            for key, value in scope.get('headers', []):
                name = key.decode('ascii').lower()
                # Do not silently pick a winner for duplicate trust headers.
                if name in headers and name in ('host', 'x-ingress-path',
                                                 'x-remote-user-id', 'x-forwarded-proto', 'origin'):
                    raise Rejected('duplicate ingress header')
                headers[name] = value.decode('latin1')
            ctx = context(scope.get('client', ('', 0))[0], headers, self.origins)
            username = self.mapping.get(ctx.user_id)
            user = await self.resolve(username) if username else None
            if user is None or not user.is_active:
                raise Rejected('identity is not authorized')
            path = scope.get('path', '')
            if not path.startswith('/') or path.startswith('//'):
                raise Rejected('invalid application path')
            # WebSockets are browser-originated; prevent cross-origin hijacking.
            if scope['type'] == 'websocket' and headers.get('origin') != f'{ctx.scheme}://{ctx.host}':
                raise Rejected('invalid websocket origin')
        except (Rejected, ValueError, UnicodeError):
            if scope['type'] == 'websocket':
                await send({'type': 'websocket.close', 'code': 4403})
            elif scope['type'] == 'http':
                await send({'type': 'http.response.start', 'status': 403,
                            'headers': [(b'content-type', b'text/plain; charset=utf-8')]})
                await send({'type': 'http.response.body', 'body': b'Forbidden'})
            else:
                raise Rejected('unsupported scope') from None
            return
        adapted = dict(scope)
        # ASGI includes root_path in path; Django/Channels strip it for routing.
        adapted.update(root_path=ctx.prefix, path=ctx.prefix + path,
                       raw_path=ctx.prefix.encode() + scope.get('raw_path', path.encode()),
                       scheme=('wss' if ctx.scheme == 'https' else 'ws')
                       if scope['type'] == 'websocket' else ctx.scheme,
                       user=user, ingress=ctx)
        adapted['headers'] = [(key, value) for key, value in scope.get('headers', [])
                              if not key.lower().startswith((b'x-remote-', b'x-forwarded-', b'x-ingress-'))]
        await self.app(adapted, receive, send)
