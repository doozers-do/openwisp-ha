"""Dashboard plus UI APIs; authenticated static/media use the same identity gate."""
from django.conf import settings
from django.urls import include, path, re_path
from django.views.static import serve
from openwisp.urls import urlpatterns as upstream
from openwisp_users.api.urls import get_api_urls

urlpatterns = [
    re_path(r'^static/(?P<path>.*)$', serve, {'document_root': settings.STATIC_ROOT}),
    re_path(r'^media/(?P<path>.*)$', serve, {'document_root': settings.MEDIA_ROOT}),
    path('api/v1/', include((get_api_urls(), 'users'))),
    path('api/v1/', include('openwisp_utils.api.urls')),
    *upstream,
]
