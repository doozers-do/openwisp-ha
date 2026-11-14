"""Build asset URLs using Django's current request prefix, never cached settings."""
from django.core.files.storage import FileSystemStorage
from django.urls import get_script_prefix
from openwisp_utils.storage import CompressStaticFilesStorage
from django_loci.storage import OverwriteStorage


class PrefixURL:
    def url(self, *args, **kwargs):
        url = super().url(*args, **kwargs)
        return get_script_prefix().rstrip('/') + url if url.startswith('/') else url


class Static(PrefixURL, CompressStaticFilesStorage):
    pass


class Media(PrefixURL, FileSystemStorage):
    pass


class FloorPlan(PrefixURL, OverwriteStorage):
    pass
