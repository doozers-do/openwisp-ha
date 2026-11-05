"""Seed only disposable UI fixtures, using the real application models."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/opt/ha-openwisp')
from run import read_options
from settings import upstream_environment
from service import configure_suffixes
os.environ.update(upstream_environment(os.environ, read_options(),
    json.loads(Path('/data/openwisp/secrets.json').read_text())))
os.environ.update(MODULE_NAME='dashboard', DJANGO_SETTINGS_MODULE='openwisp.settings')
sys.path.insert(0, '/opt/ha-openwisp/dashboard')
configure_suffixes()
import django
django.setup()
from django.contrib.auth import get_user_model
from openwisp_users.models import Organization
from openwisp_controller.geo.models import Location

get_user_model().objects.create_user(username='operator', password=None, is_staff=True)
Location.objects.create(id='11111111-1111-4111-8111-111111111111',
                        name='Ingress upload fixture', type='indoor', is_mobile=True,
                        organization=Organization.objects.first())
print('PASS: isolated nonprivileged account and upload location created')
