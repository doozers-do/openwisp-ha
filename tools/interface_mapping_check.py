#!/usr/bin/env python3
"""Read-only mapping diagnostic: pipe `ha apps info SLUG --raw-json` here.

Run on the HA host with this file, or pipe SSH's output into this local command.
Only the selected interface fields are printed; never dump the supplied options.
"""
import json
import sys


def main():
    info = json.load(sys.stdin)['data']
    options = info['options']
    actual = (info.get('network') or {}).get('443/tcp')
    expected = options.get('https_port', 443)
    direct = options.get('direct_https', True)
    if direct and (actual is None or int(actual) != expected):
        print('FAIL: direct HTTPS requires host mapping 443/tcp to match https_port')
        return 1
    if not direct and actual is not None:
        print('FAIL: disabled direct HTTPS still has a saved host mapping; remove it explicitly')
        return 1
    if '8099/tcp' in (info.get('network') or {}):
        print('FAIL: ingress 8099 must never be published')
        return 1
    print(f'PASS: direct HTTPS {"on host port " + str(actual) if direct else "disabled"}; ingress port stays internal')
    return 0


if __name__ == '__main__':
    sys.exit(main())
