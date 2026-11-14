"""Exercise the production ASGI entry, with upstream Django/Channels stubbed.

The container browser fixture supplies the real Django/Channels integration.
"""
import asyncio
import importlib
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'openwisp/runtime'))
from ingress import adapter


class EntryTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(is_active=True, is_superuser=False)
        self.seen = []
        self.lookups = []
        self.headers = [(b'host', b'local.example:8123'), (b'x-forwarded-proto', b'http'),
            (b'x-ingress-path', b'/api/hassio_ingress/token'), (b'x-remote-user-id', b'a' * 32),
            (b'x-openwisp-peer', b'172.30.32.2')]

        async def app(scope, receive, send):
            self.seen.append(scope)
            await send({'type': 'websocket.accept'} if scope['type'] == 'websocket'
                       else {'type': 'http.response.start', 'status': 204})

        def filter_user(**kwargs):
            self.lookups.append(kwargs)
            return SimpleNamespace(first=lambda: self.user if kwargs['username'] == 'operator' else None)

        def async_resolver(function):
            async def wrapped(*args):
                return function(*args)
            return wrapped

        modules = {name: ModuleType(name) for name in (
            'django', 'django.core', 'django.core.asgi', 'django.contrib', 'django.contrib.auth',
            'channels', 'channels.db', 'channels.routing', 'openwisp_controller',
            'openwisp_controller.routing', 'openwisp_network_topology', 'openwisp_network_topology.routing')}
        modules['django.core.asgi'].get_asgi_application = lambda: app
        modules['django.contrib.auth'].get_user_model = lambda: SimpleNamespace(objects=SimpleNamespace(filter=filter_user))
        modules['channels.db'].database_sync_to_async = async_resolver
        modules['channels.routing'].ProtocolTypeRouter = lambda routes: app
        modules['channels.routing'].URLRouter = lambda routes: app
        modules['openwisp_controller.routing'].get_routes = lambda: []
        modules['openwisp_network_topology.routing'].websocket_urlpatterns = []
        self.modules = modules
        self.load_entry({'HA_OPENWISP_INGRESS_ORIGINS': json.dumps(
            ['http://local.example:8123', 'https://cloud.example'])})

    def load_entry(self, origins):
        with patch.dict(sys.modules, self.modules), patch.dict(os.environ, {
                'HA_OPENWISP_INGRESS_USERS': json.dumps({'a' * 32: 'operator'})}):
            os.environ.pop('HA_OPENWISP_INGRESS_ORIGIN', None)
            os.environ.pop('HA_OPENWISP_INGRESS_ORIGINS', None)
            os.environ.update(origins)
            # Patch.dict restores the module table after loading this entry.
            sys.modules.pop('ingress.application', None)
            self.entry = importlib.import_module('ingress.application')

    def invoke(self, headers=None, *, client=('127.0.0.1', 1234), kind='http', path='/admin/'):
        self.seen.clear()
        sent = []

        async def send(message):
            sent.append(message)

        asyncio.run(self.entry.application(dict(type=kind, client=client,
            headers=self.headers if headers is None else headers,
            path=path, raw_path=path.encode()), None, send))
        return sent

    def replace(self, **changes):
        headers = [(key, value) for key, value in self.headers if key.decode() not in changes]
        headers.extend((key.encode(), value.encode()) for key, value in changes.items() if value is not None)
        return headers

    def test_both_origins_prefix_identity_and_canonical_host(self):
        for host, scheme, normalized in [('LOCAL.example:8123', 'http', 'local.example:8123'),
                                         ('CLOUD.example:443', 'https', 'cloud.example'),
                                         ('CLOUD.example:443', 'HTTPS', 'cloud.example')]:
            self.assertEqual(self.invoke(self.replace(host=host, **{'x-forwarded-proto': scheme}))[0]['status'], 204)
            seen = self.seen[0]
            self.assertEqual(seen['path'], '/api/hassio_ingress/token/admin/')
            self.assertEqual(seen['root_path'], '/api/hassio_ingress/token')
            self.assertEqual(seen['scheme'], scheme.lower())
            self.assertIs(seen['user'], self.user)
            self.assertEqual(seen['headers'], [(b'host', normalized.encode())])
            self.assertFalse(seen['user'].is_superuser)

    def test_single_origin_list_entry(self):
        self.load_entry({'HA_OPENWISP_INGRESS_ORIGINS': '["http://local.example:8123"]'})
        self.assertEqual(self.invoke()[0]['status'], 204)
        self.assertEqual(self.invoke(self.replace(host='cloud.example', **{'x-forwarded-proto': 'https'}))[0]['status'], 403)

    def test_removed_environment_fallback_is_rejected(self):
        with self.assertRaises(ValueError):
            self.load_entry({'HA_OPENWISP_INGRESS_ORIGIN': 'http://local.example:8123'})

    def test_rejection_matrix(self):
        cases = [self.replace(**changes) for changes in (
            {'host': 'unknown.example'}, {'host': 'local.example:8124'},
            {'host': 'local.example:8123', 'x-forwarded-proto': 'https'},
            {'host': 'cloud.example', 'x-forwarded-proto': 'http'},
            {'x-forwarded-proto': 'http,https'}, {'x-forwarded-proto': None},
            {'x-remote-user-id': None}, {'x-remote-user-id': 'b' * 32},
            {'x-remote-user-id': 'malformed'}, {'x-ingress-path': '/bad'},
            {'x-openwisp-peer': None}, {'x-openwisp-peer': '172.30.32.3'},
            {'x-openwisp-peer': 'ÿ'}, {'host': 'host\r\nX-Forged: value'})]
        for key in ('host', 'x-forwarded-proto', 'x-remote-user-id', 'x-ingress-path', 'x-openwisp-peer', 'origin'):
            cases.append(self.headers + [(key.upper().encode(), b'duplicate')]
                         + ([(b'origin', b'http://local.example:8123')] if key == 'origin' else []))
        cases.append(self.headers + [(b'\xff', b'invalid')])
        for headers in cases:
            with self.subTest(headers=headers):
                self.assertEqual(self.invoke(headers)[0]['status'], 403)
                self.assertEqual(self.seen, [])
        for peer in ('172.30.32.3', '::1', '192.168.1.2'):
            self.assertEqual(self.invoke(client=(peer, 1))[0]['status'], 403)
        self.assertEqual(self.invoke(client=None)[0]['status'], 403)
        self.assertEqual(self.invoke(path='//evil')[0]['status'], 403)
        self.user.is_active = False
        self.assertEqual(self.invoke()[0]['status'], 403)
        self.user = None
        self.assertEqual(self.invoke()[0]['status'], 403)

    def test_forged_forwarded_host_and_identity_headers_are_stripped(self):
        headers = self.headers + [(b'x-forwarded-host', b'unknown.example'),
            (b'x-forwarded-for', b'172.30.32.2'), (b'x-remote-user-groups', b'admins')]
        self.assertEqual(self.invoke(headers)[0]['status'], 204)
        self.assertEqual(self.seen[0]['headers'], [(b'host', b'local.example:8123')])
        self.assertEqual(self.lookups, [{'username': 'operator', 'is_active': True}])

    def test_websockets_require_the_current_origin(self):
        for host, scheme, expected in [('local.example:8123', 'http', 'ws'), ('cloud.example', 'https', 'wss')]:
            base = self.replace(host=host, **{'x-forwarded-proto': scheme})
            origin = f'{scheme}://{host}'
            self.assertEqual(self.invoke(base + [(b'origin', origin.encode())], kind='websocket'),
                             [{'type': 'websocket.accept'}])
            self.assertEqual(self.seen[0]['scheme'], expected)
            for foreign in ('http://local.example:8123', 'https://cloud.example', 'https://unknown.example', 'null', ''):
                if foreign == origin:
                    continue
                headers = base + ([(b'origin', foreign.encode())] if foreign else [])
                self.assertEqual(self.invoke(headers, kind='websocket'), [{'type': 'websocket.close', 'code': 4403}])
                self.assertEqual(self.seen, [])

    def test_sanitized_rejection_logging_is_bounded(self):
        with patch.dict(adapter._last_rejection, {}, clear=True), patch.object(adapter, 'monotonic', return_value=100):
            with self.assertLogs('ingress.adapter', level='WARNING') as captured:
                for _ in range(10):
                    self.invoke(self.replace(host='private-host-secret.example'))
                    adapter.log_rejection(ValueError('private-path-token'))
            self.assertEqual(len(captured.output), 2)
            self.assertTrue(all('private' not in line for line in captured.output))
            self.assertLessEqual(len(adapter._last_rejection), len(adapter.REJECTION_REASONS))
