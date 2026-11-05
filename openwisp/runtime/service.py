#!/usr/bin/env python3
"""Bound readiness and run the unchanged upstream OpenWISP ASGI application."""

import os
import sys
import time

import redis
from redis.backoff import NoBackoff
from redis.retry import Retry
import tldextract


def configure_suffixes():
    # The image already contains a suffix snapshot; startup must also work offline.
    tldextract.extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def main():
    client = redis.Redis(host="127.0.0.1", port=6379, password=os.environ["REDIS_PASS"],
                         socket_connect_timeout=1, socket_timeout=1,
                         retry=Retry(NoBackoff(), 0))
    timeout = int(os.environ["HA_OPENWISP_STARTUP_TIMEOUT"])
    deadline = time.monotonic() + timeout
    while True:
        try:
            client.ping()
            break
        except redis.RedisError:
            if time.monotonic() >= deadline:
                print(f"OpenWISP ERROR: internal Redis unavailable after {timeout}s",
                      file=sys.stderr, flush=True)
                return 1
            time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    print("OpenWISP: internal Redis ready at 127.0.0.1:6379; launching upstream Daphne",
          flush=True)
    os.chdir("/opt/ha-openwisp/dashboard" if '--ingress' in sys.argv else "/opt/openwisp")
    sys.path.insert(0, os.getcwd())
    configure_suffixes()
    from daphne.cli import CommandLineInterface
    CommandLineInterface().run([
        "-b", "127.0.0.1", "-p", "8003" if '--ingress' in sys.argv else "8002",
        "ingress.application:application" if '--ingress' in sys.argv else "openwisp.asgi:application",
        "--access-log", "/dev/null",
    ])
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
