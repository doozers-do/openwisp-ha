"""Exercise the ingress menu wrapper without requiring the image's Django install."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
import unittest
from unittest.mock import patch


class IngressMenuTests(unittest.TestCase):
    def setUp(self):
        self.prefix = '/api/hassio_ingress/one'
        self.entries = [
            {'id': 10, 'url': '/admin', 'label': 'Home', 'icon': 'home'},
            {'id': 20, 'label': 'Devices', 'sub_items': [{'url': '/other'}]},
            {'id': 899, 'url': '/admin/openwisp-system-info/', 'label': 'System info'},
        ]
        self.context = {'openwisp_menu_groups': self.entries, 'other': object()}
        self.calls = []
        modules = {name: ModuleType(name) for name in (
            'django', 'django.core', 'django.core.exceptions', 'django.urls',
            'openwisp_utils', 'openwisp_utils.admin_theme',
            'openwisp_utils.admin_theme.context_processor')}
        modules['django.core.exceptions'].ImproperlyConfigured = ValueError
        modules['django.urls'].reverse = lambda name: self.prefix + {
            'admin:index': '/admin/', 'admin:ow-info': '/admin/openwisp-system-info/'}[name]
        modules['openwisp_utils.admin_theme.context_processor'].menu_groups = self.upstream
        with patch.dict(sys.modules, modules):
            spec = importlib.util.spec_from_file_location('ingress_menu_under_test',
                Path(__file__).resolve().parents[2] / 'openwisp/runtime/ingress/menu.py')
            self.menu = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(self.menu)

    def upstream(self, request):
        self.calls.append(request)
        return dict(self.context)

    def test_request_local_urls_and_other_context_are_preserved(self):
        request = object()
        first = self.menu.menu_groups(request)
        self.assertEqual(self.calls, [request])
        self.assertIs(first['other'], self.context['other'])
        self.assertIs(first['openwisp_menu_groups'][1], self.entries[1])
        self.assertEqual(first['openwisp_menu_groups'][0]['label'], 'Home')
        self.assertEqual(first['openwisp_menu_groups'][0]['url'], self.prefix + '/admin/')
        self.assertEqual(self.entries[0]['url'], '/admin')
        self.prefix = '/api/hassio_ingress/two'
        second = self.menu.menu_groups(request)
        self.assertEqual(second['openwisp_menu_groups'][2]['url'],
                         self.prefix + '/admin/openwisp-system-info/')
        self.assertEqual(first['openwisp_menu_groups'][0]['url'], '/api/hassio_ingress/one/admin/')

    def test_missing_changed_or_duplicate_default_fails_clearly(self):
        for entries in (self.entries[1:],
                        [{**self.entries[0], 'url': '/custom'}, *self.entries[1:]],
                        [*self.entries, self.entries[0]]):
            with self.subTest(entries=entries):
                self.context['openwisp_menu_groups'] = entries
                with self.assertRaisesRegex(ValueError, 'position 10 changed'):
                    self.menu.menu_groups(object())


if __name__ == '__main__':
    unittest.main()
