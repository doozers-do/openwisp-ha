"""Integration checks executed inside the single app's real runtime."""

import asyncio
import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import sys
import urllib.error
import urllib.request

import redis
from channels_redis.core import RedisChannelLayer

sys.path.insert(0, "/opt/ha-openwisp")
from run import environment
from service import configure_suffixes


async def channel_roundtrip(password):
    layer = RedisChannelLayer(hosts=[{
        "address": "redis://127.0.0.1:6379/3", "password": password,
    }], prefix="ha-image-lifecycle", expiry=10)
    try:
        channel = await layer.new_channel()
        await layer.send(channel, {"type": "packaging.test", "value": "roundtrip"})
        result = await asyncio.wait_for(layer.receive(channel), timeout=3)
        assert result == {"type": "packaging.test", "value": "roundtrip"}, "Channel message changed"
    finally:
        await layer.close_pools()


def main():
    secret_file = Path("/data/openwisp/secrets.json")
    credentials = json.loads(secret_file.read_text())
    assert stat.S_IMODE(secret_file.stat().st_mode) == 0o600, "Secret file is not private"
    assert (Path("/data/openwisp/redis").stat().st_uid == pwd.getpwnam("redis").pw_uid), "Redis storage has wrong owner"
    os.environ.update(environment(credentials, 30))
    os.environ["DJANGO_SETTINGS_MODULE"] = "openwisp.settings"
    configure_suffixes()
    import django
    django.setup()
    from django.core.cache import caches

    client = redis.Redis(host="127.0.0.1", password=credentials["redis"],
                         socket_timeout=2, decode_responses=True)
    assert client.ping(), "Authenticated internal Redis is unavailable"
    assert client.info()["redis_version"] == "8.0.2", "Unexpected Redis runtime version"
    try:
        redis.Redis(host="127.0.0.1", socket_timeout=2).ping()
    except redis.AuthenticationError:
        pass
    else:
        raise AssertionError("Redis accepted unauthenticated access")
    cache = caches["default"]
    if sys.argv[1] == "write":
        cache.set("image-lifecycle-persistence", "known-test-data", timeout=None)
    assert cache.get("image-lifecycle-persistence") == "known-test-data", "Django cache data did not survive"
    client.save()
    asyncio.run(channel_roundtrip(credentials["redis"]))
    try:
        urllib.request.urlopen("http://127.0.0.1:8002/", timeout=3)
    except urllib.error.HTTPError as error:
        assert error.code == 404, f"Expected upstream ASGI response, got {error.code}"
    else:
        raise AssertionError("Expected upstream 404 for the unspecified root route")
    fingerprint = hashlib.sha256(secret_file.read_bytes()).hexdigest()
    print(json.dumps({"credentials_sha256": fingerprint, "cache": "pass", "channels": "pass", "asgi": "pass"}))


if __name__ == "__main__":
    main()
