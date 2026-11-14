#!/usr/bin/env python3
"""Stop the app when a required process exits, including after successful startup."""

import os
from pathlib import Path
import sys

from supervisor import childutils


APPLICATIONS = ("dashboard", "api", "websocket", "celery", "scheduler", "nginx")
if os.environ.get('HA_OPENWISP_INGRESS_ENABLED') == 'True':
    APPLICATIONS += ('ingress',)


def main():
    while True:
        headers, payload = childutils.listener.wait()
        details = childutils.get_headers(payload)
        name = details.get("processname")
        if (name == "initialize" and headers["eventname"] == "PROCESS_STATE_EXITED"
                and details.get("expected") == "1"
                and Path("/run/ha-openwisp/initialization/complete").is_file()):
            childutils.listener.ok()
            supervisor = childutils.getRPCInterface(os.environ).supervisor
            for role in APPLICATIONS:
                supervisor.startProcess(role, False)
            continue
        if name in {"redis", "postgres", "influxdb", "initialize", *APPLICATIONS}:
            Path("/run/ha-openwisp/failure").touch()
            print(f"OpenWISP ERROR: {details['processname']} reported {headers['eventname']}",
                  file=sys.stderr, flush=True)
            childutils.listener.ok()
            childutils.getRPCInterface(os.environ).supervisor.shutdown()
            return
        childutils.listener.ok()


if __name__ == "__main__":
    main()
