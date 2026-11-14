#!/usr/bin/env python3
"""Check repository discovery using Supervisor's config.* filename pattern."""

from pathlib import Path
import sys


def find_configs(root):
    # Supervisor store/data.py::_find_app_configs uses config.* and then checks
    # the final suffix. In particular, config.example.yaml IS an app definition.
    # https://github.com/home-assistant/supervisor/blob/main/supervisor/store/data.py
    return sorted(
        path.relative_to(root).as_posix()
        for path in root.glob("**/config.*")
        if path.suffix in {".yaml", ".yml", ".json"}
        and not any(part.startswith(".") or part == "rootfs"
                    for part in path.relative_to(root).parts)
    )


def main():
    root = Path(__file__).resolve().parents[1]
    configs = find_configs(root)
    if configs != ["openwisp/config.yaml"]:
        print("FAIL: expected only openwisp/config.yaml; Supervisor discovers:", file=sys.stderr)
        print("\n".join(configs), file=sys.stderr)
        return 1
    print("PASS: Supervisor discovers exactly one app: openwisp/config.yaml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
