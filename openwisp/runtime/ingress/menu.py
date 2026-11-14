"""Generate the pinned admin theme's default links for the current ingress request."""
from django.core.exceptions import ImproperlyConfigured
from django.urls import reverse
from openwisp_utils.admin_theme.context_processor import menu_groups as upstream_menu_groups


def menu_groups(request):
    context = upstream_menu_groups(request)
    entries = context['openwisp_menu_groups']
    replacements = {10: ('/admin', 'admin:index'),
                    899: ('/admin/openwisp-system-info/', 'admin:ow-info')}
    for position, (expected_url, _) in replacements.items():
        matches = [item for item in entries if item.get('id') == position]
        if len(matches) != 1 or matches[0].get('url') != expected_url or matches[0].get('sub_items'):
            raise ImproperlyConfigured(f'Pinned admin menu entry at position {position} changed')
    context['openwisp_menu_groups'] = [
        {**item, 'url': reverse(replacements[item['id']][1])}
        if item.get('id') in replacements else item
        for item in entries
    ]
    return context
