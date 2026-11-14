"""Adapt reviewed, pinned frontend URL construction at image build time.

These are source changes before static collection, not response substitutions.
Counts deliberately fail the build when the pinned upstream source changes.
"""
from pathlib import Path
import re
import site

ROOT = Path(site.getsitepackages()[0])
FILES = {
    'openwisp_ipam/static/openwisp-ipam/js/subnet.js': 3,
    'openwisp_notifications/static/openwisp-notifications/js/notifications.js': 3,
    'openwisp_notifications/static/openwisp-notifications/js/preferences.js': 4,
    'openwisp_notifications/static/openwisp-notifications/js/object-notifications.js': 2,
    'openwisp_controller/config/static/config/js/widget.js': 1,
    'openwisp_monitoring/monitoring/static/monitoring/js/chart-utils.js': 1,
    'openwisp_monitoring/device/static/monitoring/js/device-map.js': 1,
}


def replace(path, old, new, count=1):
    source = path.read_text()
    if source.count(old) != count:
        raise RuntimeError(f'Pinned frontend changed: {path.name}')
    path.write_text(source.replace(old, new))


def main():
    literal = re.compile(r'([\x27\x22\x60])/(?:api|ws|admin)/[^\x27\x22\x60]*\1')
    for name, expected in FILES.items():
        path = ROOT / name
        text, count = literal.subn(lambda match: f'window.openwispPath({match[0]})', path.read_text())
        if count != expected:
            raise RuntimeError(f'Pinned frontend changed: {name}')
        path.write_text(text)
    replace(ROOT / 'openwisp_utils/admin_theme/templates/admin/base_site.html',
            '{% block extrastyle %}', '''{% block extrahead %}
{% if openwisp_ingress_prefix %}<meta name="referrer" content="strict-origin">{% endif %}
{{ block.super }}
<script src="{% url 'admin:jsi18n' %}"></script>
<script>
window.openwispPath = function(path) {
    const prefix = "{{ openwisp_ingress_prefix|default:''|escapejs }}";
    return prefix + path;
};
</script>
{% endblock %}
{% block extrastyle %}''')
    replace(ROOT / 'openwisp_monitoring/monitoring/static/monitoring/js/chart-utils.js',
            '    $(document).ready(function () {',
            '    $(document).ready(function () {\n'
            '      if (!$("#monitoring-timeseries-default-time").length) return;')
    replace(ROOT / 'openwisp_notifications/templates/admin/base_site.html',
            '${notificationApiHost.host}/ws/notification/',
            '${notificationApiHost.host}{{ openwisp_ingress_prefix|default:""|escapejs }}/ws/notification/')
    replace(ROOT / 'openwisp_network_topology/templates/netjsongraph/netjsongraph-script.html',
            '${apiHost}/ws/network-topology/',
            '${apiHost}{{ openwisp_ingress_prefix|default:""|escapejs }}/ws/network-topology/')


if __name__ == '__main__':
    main()
