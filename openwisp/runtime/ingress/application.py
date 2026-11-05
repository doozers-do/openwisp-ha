"""Separate ASGI entry point: only the peer-restricted Nginx listener can call it."""
import json
import os

os.environ['DJANGO_SETTINGS_MODULE'] = 'ingress.settings'
from django.core.asgi import get_asgi_application
django_app = get_asgi_application()

from channels.db import database_sync_to_async
from channels.routing import ProtocolTypeRouter, URLRouter
from django.contrib.auth import get_user_model
from openwisp_controller.routing import get_routes
from openwisp_network_topology.routing import websocket_urlpatterns
from .adapter import ASGI


@database_sync_to_async
def resolve(username):
    return get_user_model().objects.filter(username=username, is_active=True).first()


wrapped = ASGI(ProtocolTypeRouter({
    'http': django_app,
    'websocket': URLRouter(get_routes() + websocket_urlpatterns),
}), origins={os.environ['HA_OPENWISP_INGRESS_ORIGIN']},
    mapping=json.loads(os.environ['HA_OPENWISP_INGRESS_USERS']), resolve=resolve)


async def application(scope, receive, send):
    scope = dict(scope)
    # The loopback transport is Nginx; it overwrites this private header from
    # $remote_addr. A non-loopback caller cannot assert this transport identity.
    if scope.get('client', ('', 0))[0] == '127.0.0.1':
        peers = [v.decode('ascii') for k, v in scope['headers'] if k.lower() == b'x-openwisp-peer']
        scope['client'] = (peers[0] if len(peers) == 1 else '', 0)
    await wrapped(scope, receive, send)
