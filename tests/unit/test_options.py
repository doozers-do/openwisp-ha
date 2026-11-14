import importlib.util
from pathlib import Path
import unittest
import sys
import json

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'openwisp/runtime'))
from ingress.origins import environment_origins

spec = importlib.util.spec_from_file_location('options', Path(__file__).resolve().parents[2] / 'openwisp/runtime/settings.py')
options = importlib.util.module_from_spec(spec)
spec.loader.exec_module(options)


class InterfaceOptions(unittest.TestCase):
    def setUp(self):
        self.raw = dict(direct_https=False, ingress_enabled=True,
                        ingress_origins=['http://homeassistant.local:8123'],
                        admin_username='nathan', admin_email='test@example.org',
                        admin_password='dummy-password', django_secret_key='x' * 64,
                        runtime_mode='production', startup_timeout=60)

    def test_ingress_without_dns_and_tls(self):
        result = options.validate_options(self.raw)
        self.assertFalse(result['direct_https'])
        self.assertEqual(result['ingress_users'], {})
        self.assertEqual(result['ingress_origins'], self.raw['ingress_origins'])

    def test_removed_single_origin_requires_manual_migration(self):
        for legacy in ('', 'https://saved.example'):
            with self.subTest(legacy=legacy), self.assertRaisesRegex(options.OptionError, 'removed in 1.0.0'):
                options.validate_options({**self.raw, 'ingress_origin': legacy})
        migrated = {**self.raw, 'ingress_origins': ['https://SAVED.example:443']}
        self.assertEqual(options.validate_options(migrated)['ingress_origins'], ['https://saved.example'])

    def test_direct_https_without_ingress_origins(self):
        direct = {**self.raw, 'direct_https': True, 'ingress_enabled': False,
                  'dashboard_domain': 'controller.example', 'api_domain': 'controller.example'}
        del direct['ingress_origins']
        self.assertEqual(options.validate_options(direct, https_port=443)['ingress_origins'], [])
        self.assertEqual(options.validate_options({**direct, 'ingress_origins': []}, https_port=443)['ingress_origins'], [])
        with self.assertRaises(options.OptionError):
            options.validate_options({**direct, 'ingress_origins': None})
        with self.assertRaises(options.OptionError):
            options.validate_options({key: value for key, value in self.raw.items() if key != 'ingress_origins'})

    def test_list_validation_cookie_isolation_and_canonicalization(self):
        valid = {**self.raw, 'ingress_origins':
                 ['HTTP://LOCAL.example:80', 'https://CLOUD.example:443', 'http://[2001:DB8::1]:8123']}
        self.assertEqual(options.validate_options(valid)['ingress_origins'],
                         ['http://local.example', 'https://cloud.example', 'http://[2001:db8::1]:8123'])
        invalid = [None, {}, 'https://cloud.example', [], [''], [None], [123],
                   ['https://host', 'HTTPS://HOST:443'],
                   ['http://host:8123', 'https://host'],
                   ['http://host:8123', 'http://host:8124'],
                   [f'https://host{index}.example' for index in range(17)]]
        malformed = ['https://host/', 'https://host?', 'https://host#', 'https://*.example',
                     'https://user:pass@host', 'https://host:0', 'https://host:65536',
                     'https://host:', 'https://host:abc', 'https://[::1]evil',
                     'https://[:::1]', 'https://-host', 'https://host..test',
                     'https://host.', 'https://host\\evil', 'https://host\n',
                     'https://host,other', 'https://host/path', ' https://host',
                     'https://höst', 'https://K.example', 'http://127.1', 'http://0127.0.0.1']
        for entries in invalid + [[value] for value in malformed]:
            with self.subTest(entries=entries), self.assertRaises(options.OptionError):
                options.validate_options({**valid, 'ingress_origins': entries})

    def test_origin_environment_is_validated_json_without_stale_legacy(self):
        configured = options.validate_options({**self.raw,
            'ingress_origins': ['http://local.example:8123', 'https://cloud.example']})
        env = options.upstream_environment({'HA_OPENWISP_INGRESS_ORIGIN': 'https://stale.example'},
            configured, dict(redis='r', database='d', influxdb='i'))
        self.assertEqual(json.loads(env['HA_OPENWISP_INGRESS_ORIGINS']), configured['ingress_origins'])
        self.assertNotIn('HA_OPENWISP_INGRESS_ORIGIN', env)

    def test_environment_requires_json_list(self):
        self.assertEqual(environment_origins({'HA_OPENWISP_INGRESS_ORIGINS': '["HTTPS://Saved.example:443"]'}),
                         ['https://saved.example'])
        with self.assertRaises(ValueError):
            environment_origins({'HA_OPENWISP_INGRESS_ORIGIN': 'https://saved.example'})
        for raw in ('null', '{}', '[]', 'invalid-json', '["https://host/path"]'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                environment_origins({'HA_OPENWISP_INGRESS_ORIGINS': raw})
        with self.assertRaises(ValueError):
            environment_origins({})

    def test_invalid_origins_and_mappings(self):
        for origin in ('http://evil/x', 'http://user@host', 'http://host#x',
                       'http://host:invalid', 'javascript:alert(1)', ''):
            with self.subTest(origin=origin), self.assertRaises(options.OptionError):
                options.validate_options({**self.raw, 'ingress_origins': [origin]})
        for entries in ({}, [{'ha_user_id': 'nathan', 'openwisp_username': 'nathan'}]):
            with self.assertRaises(options.OptionError):
                options.validate_options({**self.raw, 'ingress_users': entries})

    def test_canonical_external_url_and_explicit_mapping(self):
        result = options.validate_options({**self.raw, 'direct_https': True,
            'api_domain': 'controller.example.org', 'dashboard_domain': 'controller.example.org',
            'external_url': 'https://controller.example.org:8443',
            'ingress_users': [{'ha_user_id': 'a' * 32, 'openwisp_username': 'nathan'}]}, https_port=8443)
        self.assertEqual(result['ingress_users'], {'a' * 32: 'nathan'})
        self.assertEqual(result['https_port'], 8443)
        self.assertEqual(result['external_url'], 'https://controller.example.org:8443')
        with self.assertRaises(options.OptionError):
            options.validate_options({**self.raw, 'external_url': 'https://wrong.example.org'})
