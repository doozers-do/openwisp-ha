"""Bind each request to the explicitly mapped account and scope its cookies."""
from django.conf import settings
from django.contrib.auth import login
from django.http import HttpResponseForbidden


class Identity:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.scope.get('user')
        if user is None or not user.is_active:
            return HttpResponseForbidden()
        if request.user.pk != user.pk:
            login(request, user, backend='openwisp_users.backends.UsersAuthenticationBackend')
        request.user = user
        return self.get_response(request)


class ResponsePolicy:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.headers.pop('Strict-Transport-Security', None)
        response['X-Frame-Options'] = 'SAMEORIGIN'
        prefix = request.scope['ingress'].prefix
        if request.path_info == '/admin/logout/' and request.method == 'POST' and response.status_code < 400:
            for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
                response.delete_cookie(name, path=prefix + '/', samesite='Lax')
        for name in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME):
            if name in response.cookies:
                cookie = response.cookies[name]
                cookie['path'] = prefix + '/'
                cookie['domain'] = ''
                cookie['secure'] = request.is_secure()
        return response


def template_context(request):
    return {'openwisp_ingress_prefix': request.scope['ingress'].prefix}
