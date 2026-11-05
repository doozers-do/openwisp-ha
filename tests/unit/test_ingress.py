"""Trust-boundary tests for the isolated ingress adapter, without a live listener."""
import asyncio
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest

spec = importlib.util.spec_from_file_location('ingress_adapter',
    Path(__file__).resolve().parents[2] / 'openwisp/runtime/ingress/adapter.py')
adapter = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = adapter
spec.loader.exec_module(adapter)


class IngressTests(unittest.TestCase):
    def setUp(self):
        self.user = SimpleNamespace(is_active=True, is_superuser=False)
        self.uid = 'a' * 32
        self.options = dict(origins={'http://homeassistant.local:8123'},
                            mapping={self.uid: 'operator'}, resolve=lambda name: self.user)
        self.env = dict(REMOTE_ADDR=adapter.SUPERVISOR, PATH_INFO='/admin/',
                        HTTP_HOST='homeassistant.local:8123', HTTP_X_FORWARDED_PROTO='http',
                        HTTP_X_INGRESS_PATH='/api/hassio_ingress/token_A',
                        HTTP_X_REMOTE_USER_ID=self.uid)

    def invoke(self, **changes):
        received, statuses = [], []
        wrapper = adapter.WSGI(lambda env, start: received.append(env), **self.options)
        wrapper({**self.env, **changes}, lambda status, headers: statuses.append(status))
        return received, statuses

    def test_variable_prefixes_and_http(self):
        for token in ('token_A', 'another-token'):
            received, statuses = self.invoke(HTTP_X_INGRESS_PATH='/api/hassio_ingress/' + token)
            self.assertEqual(statuses, [])
            env = received[0]
            self.assertEqual(env['SCRIPT_NAME'], '/api/hassio_ingress/' + token)
            self.assertEqual(env['PATH_INFO'], '/admin/')
            self.assertEqual(env['wsgi.url_scheme'], 'http')
            self.assertIs(env['openwisp.user'], self.user)
            self.assertFalse(env['openwisp.user'].is_superuser)
            self.assertNotIn('HTTP_X_REMOTE_USER_ID', env)
        self.assertNotIn('SCRIPT_NAME', self.env)

    def test_peer_identity_prefix_and_origin_fail_closed(self):
        cases = [dict(REMOTE_ADDR=value) for value in ('127.0.0.1', '172.30.32.3', '::1')]
        cases += [dict(HTTP_X_INGRESS_PATH=value) for value in
                  ('', '/', '//evil', '/api/hassio_ingress/../admin',
                   '/api/hassio_ingress/a%2Fb', '/api/hassio_ingress/a\r\nX:1',
                   '/api/hassio_ingress/a?x=1', '/api/hassio_ingress/a/')]
        cases += [dict(HTTP_X_REMOTE_USER_ID=value) for value in ('', 'b' * 32, 'admin')]
        cases += [dict(HTTP_HOST='evil.example'), dict(HTTP_X_FORWARDED_PROTO='https'),
                  dict(HTTP_X_FORWARDED_PROTO='http,https'), dict(PATH_INFO='//evil')]
        for changes in cases:
            with self.subTest(changes=changes):
                received, statuses = self.invoke(**changes)
                self.assertEqual(received, [])
                self.assertEqual(statuses, ['403 Forbidden'])
        self.user.is_active = False
        self.assertEqual(self.invoke()[1], ['403 Forbidden'])

    def test_verified_https_profile(self):
        self.options['origins'] = {'https://homeassistant.example.org'}
        received, statuses = self.invoke(HTTP_HOST='homeassistant.example.org', HTTP_X_FORWARDED_PROTO='https')
        self.assertEqual(statuses, [])
        self.assertEqual(received[0]['wsgi.url_scheme'], 'https')
        self.assertEqual(self.invoke(HTTP_HOST='homeassistant.example.org')[1], ['403 Forbidden'])

    def test_websocket_origin_duplicates_mapping_and_prefix(self):
        async def run():
            seen, sent = [], []

            async def app(scope, receive, send):
                seen.append(scope)

            async def resolve(name):
                return self.user

            async def send(message):
                sent.append(message)

            options = {**self.options, 'resolve': resolve}
            wrapper = adapter.ASGI(app, **options)
            headers = [(key[5:].replace('_', '-').lower().encode(), value.encode())
                       for key, value in self.env.items() if key.startswith('HTTP_')]
            headers.append((b'origin', b'http://homeassistant.local:8123'))
            scope = dict(type='websocket', client=(adapter.SUPERVISOR, 1234),
                         headers=headers, path='/ws/notification/', raw_path=b'/ws/notification/')
            await wrapper(scope, None, send)
            self.assertEqual(seen[0]['root_path'], self.env['HTTP_X_INGRESS_PATH'])
            self.assertEqual(seen[0]['path'], self.env['HTTP_X_INGRESS_PATH'] + '/ws/notification/')
            self.assertEqual(seen[0]['scheme'], 'ws')
            self.assertIs(seen[0]['user'], self.user)
            self.assertEqual(sent, [])
            for extra in ((b'x-remote-user-id', b'b' * 32), (b'origin', b'https://evil.example')):
                seen.clear()
                await wrapper({**scope, 'headers': headers + [extra]}, None, send)
                self.assertEqual(seen, [])
                self.assertEqual(sent[-1], {'type': 'websocket.close', 'code': 4403})

        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
