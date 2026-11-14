"""Development-only worker probes, injected by https_endpoints_check.py."""
from celery import shared_task
from celery.signals import task_postrun
from django.core.cache import cache
import requests


@shared_task(name='https.management_probe')
def management_probe(url):
    response = requests.get(url, timeout=5)
    response.raise_for_status()
    return response.text


@task_postrun.connect
def record_completion(task_id=None, task=None, retval=None, state=None, **kwargs):
    cache.set('https-result-' + task_id, {'name': task.name, 'state': state, 'value': retval}, 120)
