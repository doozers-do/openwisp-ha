# Changelog

## 0.5.1

- Consolidate the four single-concurrency Celery workers into one worker consuming all four queues.
- Add the `celery_concurrency` option (default 2, range 1–4) and validate its value before Supervisor starts.
- Preserve required-worker failure handling, queue probes, agent workflows and independent persistent Celery beat.

## 0.5.0

- Add Supervisor-only ingress with explicit Home Assistant user ID to existing OpenWISP account mappings; retain organization/object permissions.
- Adapt API, asset, media and WebSocket URL generation to variable ingress prefixes, with separate settings and cookie names for direct and ingress access.
- Default new installs to no published web ports and allow ingress-only startup without TLS files or public DNS.
- Retain optional authenticated direct HTTPS and validate its canonical external URL; add a read-only saved-port mapping diagnostic.
- Preserve the 0.4.3 HSTS recovery policy. Existing installations must recover browsers before retiring the old HTTPS endpoint.
- Qualify navigation, forms/CSRF, uploads, logout, identity isolation and WebSockets in Chromium and retain direct HTTPS regression coverage.

## 0.4.3

- Replace inherited HSTS with package-owned headers; suppress Django/upstream HSTS.
- Clear the known cached policy only for `homeassistant.local` with exactly one `max-age=0` response header. Other hostnames emit no HSTS.
- Retain verified dual-stack HTTPS and the existing port mapping during browser recovery. Visit the existing trusted HTTPS URL in every affected browser before moving ports.
- Add retained-cache coexistence tests alongside an HTTP-only service on port 8123.

## 0.4.2

- Listen on IPv4 and IPv6 for HTTPS, including hostname rejection, so dual-stack Home Assistant Docker forwarding reaches Nginx.
- Validate certificate-verified HTTPS and unknown-host rejection over each address family.

## 0.4.1

- Generate 825-day server certificates for macOS private-CA compatibility; retain the indefinite-validity CA and validate with native macOS SSL policy.
- Replace requested certificate files without creating backups.

- Support a shared dashboard/API hostname such as homeassistant.local with host-only login cookies.
- Add an OpenSSL certificate-generation/renewal helper and document certificate transfer and client trust.

## 0.4.0

- Start pinned dashboard/API, WebSocket, four single-concurrency worker queues and persistent Celery beat after initialization.
- Route separate dashboard/API names, static/media and WebSocket through native Nginx using the pinned upstream TLS template.
- Serve HTTPS inside the app on 443/tcp, read certificate files from Home Assistant's read-only ssl directory, and validate TLS inputs before starting services.
- Support an alternate host HTTPS port through the https_port option; no external proxy service is required.
- Keep private files off public routes and omit request access logs that could contain agent keys.
- Validate HTTPS sign-in, API, WebSocket, static/media, upstream task completion, worker networking and required-role failure recovery.

## 0.3.0

- Start authenticated loopback PostgreSQL/PostGIS, Redis and InfluxDB with private persistent storage.
- Complete upstream migrations, initial data, persistent SSH keys and static collection before starting WebSocket.
- Seed the supplied administrator before upstream bootstrap; retain existing accounts, credentials and data on subsequent starts.
- Stop the app on dependency or initialization failure and document restart recovery and storage ownership.
- Add an offline integration check for fresh startup, persistence, database interruption and migration failure.

## 0.2.0

- Added required dashboard/API names, administrator credentials, Django secret, runtime mode, and bounded timeout options.
- Added option validation and translation to upstream settings before the internal supervisor starts.
- Declared optional HTTP/HTTPS host port mappings for the later Nginx endpoint.

## 0.1.1

- Package pinned PostgreSQL/PostGIS, InfluxDB and Nginx/Brotli dependencies with pinned PostgreSQL/PostGIS, InfluxDB and Nginx/Brotli in the single image.
- Added upstream dashboard/API/proxy inputs and tested migrations, spatial data, metrics, workers, static files and proxy compatibility.
- Added uWSGI settings verified to terminate cleanly with the combined Python runtime.

## 0.1.0

- Added one OpenWISP app entry with an internal Redis and upstream ASGI runtime.
- Added private persistent credentials, coordinated shutdown and child-failure handling.
- Retired the separate Redis/client app entries. Full controller functionality is pending.
