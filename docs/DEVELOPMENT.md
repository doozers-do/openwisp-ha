# Maintainer reference

## Supported package inputs

`openwisp/config.yaml` is the only Home Assistant app definition. It currently declares `amd64`; the Docker build is pinned to `linux/amd64`. `openwisp/build-inputs.json` records the upstream release and commit, base image digest, package versions and checksums. Review and update those inputs deliberately with every image refresh.

The app combines the pinned OpenWISP Python image with Debian PostgreSQL/PostGIS, Redis and Nginx packages and the official InfluxDB static build. `openwisp/runtime/` owns Home Assistant option validation, process startup, service configuration and ingress adaptation. The wrapper starts PostgreSQL, Redis and InfluxDB before migrations/bootstrap, then starts the dashboard, API, WebSocket, worker, scheduler and Nginx. Required service failure stops the app; recovery starts the whole app against the retained data. Persistent state is under `/data/openwisp`: PostgreSQL data, InfluxDB metadata/data/WAL, Redis snapshots, media and private uploads, SSH keys, generated credentials, Celery beat schedule and collected static files. The read-only Home Assistant `ssl` mount supplies optional HTTPS certificates. One Celery worker consumes the four upstream queues with `celery_concurrency` defaulting to 2 (range 1–4). Reduced concurrency and queue sharing trade throughput and isolation; no target-host memory saving is claimed because comparative measurements are not available. A same-host baseline/candidate memory comparison remains an alpha acceptance gate. Use the same warmed app data and workload; record host/app versions, idle cgroup `memory.current` and worker/beat PSS from host `smaps_rollup` in three samples 10 seconds apart, plus peak cgroup memory during the functional queue/agent checks. Compare medians and keep the measurements with the release record.

## Maintainer commands

Run commands from the repository root. Docker checks build the actual `linux/amd64` image, use disposable containers/volumes and clean them up. They require Docker with amd64 emulation or hardware. The HTTPS check also needs host OpenSSL and Python 3; ingress browser coverage needs Node.js, Playwright and Chromium.

- `python3 tools/check_app_discovery.py` verifies that Supervisor can discover only `openwisp/config.yaml`.
- `python3 -m unittest discover -s tests/unit` runs local option and app-discovery unit tests.
- `python3 tools/check_app_options.py` checks option translation and secret-safe errors.
- `python3 tools/image_lifecycle_check.py` builds the image and checks startup, persistence, failure recovery and dependency compatibility.
- `python3 tools/service_lifecycle_check.py` checks initialization, service persistence and recovery.
- `python3 tools/https_endpoints_check.py` checks HTTPS routes, API, WebSocket, worker queues and failure recovery.
- `python3 tools/agent_workflow_check.py` checks disposable configuration and monitoring agent workflows.
- `python3 tools/ingress_check.py` checks the browser ingress flow over HTTP; add `--https` to simulate HTTPS ingress.

The checks inject scripts from `tests/fixtures/` into disposable containers. Those files are fixtures, not user utilities. `tools/tls_fixture.py` and `tools/hsts_check.py` are shared integration helpers. User support scripts are `tools/generate_local_certificate.py` (Python 3 and OpenSSL; outputs certificates outside the repository) and `tools/interface_mapping_check.py` (read-only; accepts `ha apps info APP_SLUG --raw-json` on stdin). The latter can be run as `ssh HOST 'ha apps info APP_SLUG --raw-json' | python3 tools/interface_mapping_check.py`.

## Packaging and release review

Build locally with `docker build --platform linux/amd64 -t openwisp-ha:local openwisp`. Before a release, review the single app discovery result, option schema, build input pins/checksums, process and storage behavior, user guide links, and relevant integration checks. Validate alpha platform and workload claims against actual build support. The repository license and upstream/dependency redistribution notices require a separate review before public alpha announcement; public availability of an upstream image does not establish redistribution rights.
