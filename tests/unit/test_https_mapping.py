"""Network mapping startup, URL propagation and diagnostic regressions."""

import contextlib
import io
from http.client import IncompleteRead
import json
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'openwisp/runtime'))
sys.path.insert(0, str(ROOT / 'tools'))
import run
import settings
import interface_mapping_check


class HTTPSMappingTests(unittest.TestCase):
    def setUp(self):
        self.raw = dict(direct_https=True, dashboard_domain='controller.example.org',
                        api_domain='api.example.org', admin_username='test-admin',
                        admin_email='test@example.org', admin_password='dummy-password',
                        django_secret_key='x' * 64, runtime_mode='production', startup_timeout=60)

    def read(self, info):
        with patch.dict(run.os.environ, SUPERVISOR_TOKEN='private-token'), \
                patch.object(run.Path, 'read_text', return_value=json.dumps(self.raw)), \
                patch.object(run, 'urlopen', return_value=io.StringIO(json.dumps(info))) as request:
            result = run.read_options()
        req = request.call_args.args[0]
        self.assertEqual(req.full_url, 'http://supervisor/addons/self/info')
        self.assertEqual(req.get_header('Authorization'), 'Bearer private-token')
        self.assertEqual(request.call_args.kwargs['timeout'], 5)
        return result

    def test_network_port_reaches_urls_environment_and_browser_policy(self):
        for port, suffix in ((443, ''), (8443, ':8443')):
            with self.subTest(port=port):
                configured = self.read(dict(result='ok', data={'network': {'443/tcp': port}}))
                self.assertEqual(configured['https_port'], port)
                canonical = 'https://api.example.org' + suffix
                self.assertEqual(configured['external_url'], canonical)
                env = settings.upstream_environment(
                    {'SUPERVISOR_TOKEN': 'private-token', 'HASSIO_TOKEN': 'private-token'},
                    configured, dict(redis='r', database='d', influxdb='i'))
                for key in ('HA_OPENWISP_EXTERNAL_URL', 'OPENWISP_CONTROLLER_API_HOST',
                            'OPENWISP_MONITORING_API_BASEURL', 'OPENWISP_NETWORK_TOPOLOGY_API_BASEURL'):
                    self.assertEqual(env[key], canonical)
                self.assertEqual(env['NGINX_SSL_PORT'], str(port))
                self.assertNotIn('SUPERVISOR_TOKEN', env)
                self.assertNotIn('HASSIO_TOKEN', env)
                import proxy
                # Render actual templates without requiring certificates or a running Nginx.
                upstream = "listen 443 ssl;\n${NGINX_IP6_STRING}\n"
                real_read = Path.read_text
                def template(path, *args, **kwargs):
                    if path.name == 'openwisp.ssl.template.conf':
                        return upstream
                    return real_read(path, *args, **kwargs)
                rendered = {}
                with patch.object(proxy, 'validate_certificate'), \
                        patch.object(Path, 'read_text', template), \
                        patch.object(Path, 'write_text', lambda path, text: rendered.update({path.name: text})):
                    proxy.configure_proxy(configured)
                self.assertIn(f'https://api.example.org:{port}', rendered['security-8000.conf'])
                self.assertIn(f'https://controller.example.org:{port}', rendered['security-8001.conf'])
                self.assertIn('listen 443 ssl;', rendered['nginx.conf'])
                self.assertNotIn('listen 8443', rendered['nginx.conf'])

    def test_missing_or_invalid_mapping_blocks_startup(self):
        responses = [None, [], {}, {'result': 'error'}, {'result': 'ok', 'data': None},
                     {'result': 'ok', 'data': {}}, {'result': 'ok', 'data': {'network': None}},
                     {'result': 'ok', 'data': {'network': {}}}]
        responses += [dict(result='ok', data={'network': {'443/tcp': value}})
                      for value in (None, 0, -1, 65536, True, False, 8443.0, '8443', '', [], {})]
        for info in responses:
            with self.subTest(info=info), self.assertRaisesRegex(run.StartupError, 'Network'):
                self.read(info)

    def test_supervisor_errors_are_actionable_and_do_not_disclose_secrets(self):
        errors = [URLError('private-token'), socket.timeout('private-token'),
                  HTTPError('http://supervisor', 401, 'private-token', {}, None),
                  ValueError('private-token'), IncompleteRead(b'private-token')]
        for error in errors:
            with self.subTest(error=error), patch.dict(run.os.environ, SUPERVISOR_TOKEN='private-token'), \
                    patch.object(run, 'urlopen', side_effect=error), \
                    self.assertRaises(run.StartupError) as raised:
                run.supervisor_https_port()
            self.assertIn('Supervisor', str(raised.exception))
            self.assertIn('Network', str(raised.exception))
            self.assertNotIn('private-token', str(raised.exception))
        with patch.dict(run.os.environ, {}, clear=True), patch.object(run, 'urlopen') as request, \
                self.assertRaisesRegex(run.StartupError, 'SUPERVISOR_TOKEN is missing'):
            run.supervisor_https_port()
        request.assert_not_called()

    def test_invalid_json_from_supervisor(self):
        with patch.dict(run.os.environ, SUPERVISOR_TOKEN='private-token'), \
                patch.object(run, 'urlopen', return_value=io.StringIO('invalid-json private-token')), \
                self.assertRaisesRegex(run.StartupError, 'Network'):
            run.supervisor_https_port()

    def test_ingress_only_does_not_query_supervisor(self):
        raw = {**self.raw, 'direct_https': False, 'ingress_enabled': True,
               'ingress_origins': ['http://homeassistant.local:8123']}
        with patch.dict(run.os.environ, {}, clear=True), \
                patch.object(run.Path, 'read_text', return_value=json.dumps(raw)), \
                patch.object(run, 'supervisor_https_port') as request:
            configured = run.read_options()
        request.assert_not_called()
        self.assertFalse(configured['direct_https'])

    def test_explicit_external_url_must_match_network_port(self):
        self.raw['external_url'] = 'https://api.example.org:8443'
        self.read(dict(result='ok', data={'network': {'443/tcp': 8443}}))
        with self.assertRaisesRegex(run.StartupError, 'external_url.*Network'):
            self.read(dict(result='ok', data={'network': {'443/tcp': 443}}))

    def test_legacy_option_requires_manual_removal(self):
        with self.assertRaisesRegex(settings.OptionError, 'remove https_port'):
            settings.validate_options({**self.raw, 'https_port': 9443}, https_port=8443)

    def test_mapping_diagnostic(self):
        for direct, mapping, extra, expected in (
            (True, {'443/tcp': 8443}, {}, 0), (True, {'443/tcp': 443}, {}, 0),
            (True, {}, {}, 1), (True, {'443/tcp': None}, {}, 1),
            (True, {'443/tcp': True}, {}, 1), (True, {'443/tcp': 'bad'}, {}, 1),
            (True, {'443/tcp': 0}, {}, 1), (True, {'443/tcp': 65536}, {}, 1),
            (True, {'443/tcp': 8443}, {'https_port': 8443}, 1),
            (False, {}, {}, 0), (False, {'443/tcp': 8443}, {}, 1),
            (False, {'8099/tcp': None}, {}, 1),
        ):
            with self.subTest(direct=direct, mapping=mapping, extra=extra):
                info = dict(data=dict(options={'direct_https': direct, **extra}, network=mapping))
                with patch('sys.stdin', io.StringIO(json.dumps(info))), \
                        contextlib.redirect_stdout(io.StringIO()) as output:
                    self.assertEqual(interface_mapping_check.main(), expected)
                self.assertNotIn('Traceback', output.getvalue())
