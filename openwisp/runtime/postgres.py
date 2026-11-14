"""Initialize an empty cluster once, then exec the foreground PostgreSQL server."""

import os
from pathlib import Path
import signal
import subprocess
import sys

DATA = Path("/data/openwisp/postgres")
BIN = "/usr/lib/postgresql/17/bin/"


def main():
    version = DATA / "PG_VERSION"
    if not version.exists():
        if any(DATA.iterdir()):
            print("OpenWISP ERROR: PostgreSQL storage is nonempty without PG_VERSION; "
                  "preserve /data/openwisp/postgres for recovery", file=sys.stderr, flush=True)
            return 1
        print("OpenWISP: initializing PostgreSQL cluster in /data/openwisp/postgres", flush=True)
        stopped = False
        child = None

        def stop(*_):
            nonlocal stopped
            stopped = True
            if child is not None and child.poll() is None:
                child.send_signal(signal.SIGINT)

        for signum in (signal.SIGINT, signal.SIGTERM):
            signal.signal(signum, stop)
        child = subprocess.Popen([
            BIN + "initdb", "-D", str(DATA), "--username=admin",
            "--pwfile=/run/ha-openwisp/postgres.password", "--auth-host=scram-sha-256",
            "--auth-local=peer", "--encoding=UTF8", "--locale=C.UTF-8",
        ])
        if stopped:
            child.send_signal(signal.SIGINT)
        result = child.wait(timeout=300)
        if stopped:
            return 0
        if result:
            print("OpenWISP ERROR: PostgreSQL initdb failed; existing data retained", file=sys.stderr)
            return 1
    elif version.read_text().strip() != "17":
        print("OpenWISP ERROR: /data/openwisp/postgres needs PostgreSQL 17-compatible storage; "
              "existing data retained", file=sys.stderr, flush=True)
        return 1
    else:
        print("OpenWISP: reusing PostgreSQL cluster in /data/openwisp/postgres", flush=True)
    os.execv(BIN + "postgres", [BIN + "postgres", "-D", str(DATA),
                              "-c", "config_file=/run/ha-openwisp/postgresql.conf"])


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, subprocess.TimeoutExpired):
        print("OpenWISP ERROR: PostgreSQL initialization failed; preserve /data/openwisp/postgres",
              file=sys.stderr, flush=True)
        sys.exit(1)
