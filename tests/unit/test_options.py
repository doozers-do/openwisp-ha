import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('options', Path(__file__).resolve().parents[2] / 'openwisp/runtime/settings.py')
options = importlib.util.module_from_spec(spec)
spec.loader.exec_module(options)


class InterfaceOptions(unittest.TestCase):
    def setUp(self):
        self.raw = dict(direct_https=False, ingress_enabled=True,
                        ingress_origin='http://homeassistant.local:8123',
                        admin_username='nathan', admin_email='test@example.org',
                        admin_password='dummy-password', django_secret_key='x' * 64,
                        runtime_mode='production', startup_timeout=60)

    def test_ingress_without_dns_and_tls(self):
        result = options.validate_options(self.raw)
        self.assertFalse(result['direct_https'])
        self.assertEqual(result['ingress_users'], {})

    def test_invalid_origins_and_mappings(self):
        for origin in ('http://evil/x', 'http://user@host', 'http://host#x',
                       'http://host:invalid', 'javascript:alert(1)', ''):
            with self.subTest(origin=origin), self.assertRaises(options.OptionError):
                options.validate_options({**self.raw, 'ingress_origin': origin})
        for entries in ({}, [{'ha_user_id': 'nathan', 'openwisp_username': 'nathan'}]):
            with self.assertRaises(options.OptionError):
                options.validate_options({**self.raw, 'ingress_users': entries})

    def test_canonical_external_url_and_explicit_mapping(self):
        result = options.validate_options({**self.raw, 'direct_https': True,
            'api_domain': 'controller.example.org', 'dashboard_domain': 'controller.example.org',
            'https_port': 8443, 'external_url': 'https://controller.example.org:8443',
            'ingress_users': [{'ha_user_id': 'a' * 32, 'openwisp_username': 'nathan'}]})
        self.assertEqual(result['ingress_users'], {'a' * 32: 'nathan'})
        with self.assertRaises(options.OptionError):
            options.validate_options({**self.raw, 'external_url': 'https://wrong.example.org'})
