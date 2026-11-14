"""Protocol-compatible test client for OpenWrt Config and Monitoring agents."""
import io
import json
import math
import socket
import ssl
import sys
import tarfile
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def request(url, context, data=None):
    body = None if data is None else json.dumps(data).encode()
    headers = {"Content-Type": "application/json"} if body else {}
    req = Request(url, data=body, headers=headers)
    try:
        with urlopen(req, context=context, timeout=15) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise AssertionError(
            f"Agent endpoint returned HTTP {error.code}: {url.split('?')[0]} ({detail})"
        ) from None


def extract_config(payload):
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:gz") as archive:
        files = [archive.extractfile(member).read() for member in archive.getmembers()
                 if member.isfile()]
    return b"\n".join(files)


def check_config(base, context, fixture, expected):
    query = urlencode({"key": fixture["device_key"]})
    url = f"{base}/controller/download-config/{fixture['device_id']}/?{query}"
    status, headers, payload = request(url, context)
    assert status == 200 and payload, "Config agent download returned an empty result"
    rendered = extract_config(payload)
    assert expected.encode() in rendered, (
        f"Rendered config omitted expected hostname {expected}; payload={rendered[:3000]!r}"
    )
    return rendered


def main():
    base = sys.argv[4] if len(sys.argv) > 4 else "https://api.example.org"
    fixture = json.load(open(sys.argv[1], encoding="utf-8"))
    ca_file = sys.argv[2]
    action = sys.argv[3]
    hostname = urlsplit(base).hostname
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(hostname,
                         urlsplit(base).port or 443, type=socket.SOCK_STREAM)})
    assert addresses, "Agent hostname did not resolve"
    context = ssl.create_default_context(cafile=ca_file)

    if action == "config-before":
        check_config(base, context, fixture, fixture["before"])
        print(f"PASS: verified TLS hostname {hostname} resolved to {', '.join(addresses)}; config agent retrieved the initial template")
    elif action == "config-after":
        check_config(base, context, fixture, fixture["after"])
        print("PASS: config agent retrieved the changed template through the controller endpoint")
    elif action == "monitoring":
        # Match the upstream monitoring agent's URL, key auth, NetJSON payload,
        # timestamp format, and current-sample flag.
        stamp = datetime.now(timezone.utc).replace(microsecond=123456)
        agent_time = stamp.strftime("%d-%m-%Y_%H:%M:%S.%f")
        sample_value = 987654321
        sample = {
            "type": "DeviceMonitoring",
            "general": {"local_time": int(stamp.timestamp()), "uptime": 4321},
            "resources": {
                "cpus": 1,
                "memory": {"total": 249774080, "shared": 86016, "free": 224497664,
                           "cached": 6774784, "available": 223397664, "buffered": 974848},
                "load": [0, 0, 0],
            },
            "interfaces": [{
                "name": "agent-test0", "type": "ethernet", "up": True,
                "statistics": {"rx_bytes": sample_value, "tx_bytes": sample_value // 2,
                               "collisions": 0, "multicast": 0, "rx_dropped": 0,
                               "tx_dropped": 0},
            }],
        }
        query = urlencode({"key": fixture["device_key"], "time": agent_time, "current": "true"})
        url = f"{base}/api/v1/monitoring/device/{fixture['device_id']}/?{query}"
        status, _, _ = request(url, context, sample)
        assert status == 200, "Monitoring agent submission was not accepted"

        # Read back through OpenWISP's device monitoring API (the same API the
        # dashboard uses), waiting for its Celery-to-Influx write to complete.
        read_query = urlencode({"key": fixture["device_key"], "status": "true", "timezone": "UTC"})
        read_url = f"{base}/api/v1/monitoring/device/{fixture['device_id']}/?{read_query}"
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            status, _, payload = request(read_url, context)
            result = json.loads(payload)
            encoded = json.dumps(result, sort_keys=True)
            axis = result.get("x") or []
            traffic = next((chart for chart in result.get("charts", [])
                            if chart.get("title") == "Traffic: agent_test0"), None)
            summary = traffic.get("summary", {}) if traffic else {}
            chart_matches = (
                math.isclose(summary.get("download", 0), 0.99, abs_tol=0.01)
                and math.isclose(summary.get("upload", 0), 0.49, abs_tol=0.01)
            )
            if (status == 200 and result.get("id") == fixture["device_id"]
                    and fixture["device_name"] in encoded and str(sample_value) in encoded
                    and chart_matches and axis):
                expected_bucket = stamp.astimezone(timezone.utc).replace(
                    tzinfo=None, minute=0, second=0, microsecond=0
                )
                assert datetime.fromisoformat(axis[-1]) == expected_bucket, \
                    f"Monitoring API returned sample/device but not its submitted time bucket; x={axis[-1:]!r}"
                print(f"PASS: OpenWISP monitoring API shows device {fixture['device_name']}, sample {sample_value}, and timestamp {agent_time}")
                return
            time.sleep(1)
        raise AssertionError("Submitted monitoring sample did not appear in the OpenWISP device API with its identity, value, and timestamp")
    else:
        raise SystemExit("expected config-before, config-after, or monitoring")


if __name__ == "__main__":
    main()
