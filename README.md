# OpenWISP for Home Assistant

OpenWISP is a network configuration and monitoring controller packaged as one Home Assistant app, with its database, cache, metrics store, web server and workers. It is intended for Home Assistant OS or another Supervisor based installation on **amd64** hardware. Home Assistant Container is not supported because it does not provide Supervisor's app store.

This project is experimental. Resource use and workload limits have not been measured across supported target hosts; size the host for your intended device count and workload, then monitor it. The app starts manually after a Home Assistant OS reboot. Device and external API access require optional direct HTTPS configuration. No VPN, RADIUS, mail delivery or firmware upgrade worker is included.

## Install and configure

1. In Home Assistant, open **Settings → Apps → App store → ⋮ → Repositories** and add `https://github.com/doozers-do/openwisp-ha`.
2. Install **OpenWISP**.
3. In its Configuration page, provide `admin_username`, `admin_email`, `admin_password` and `django_secret_key`. Use a strong password and a random secret of at least 50 characters. Keep the Django secret unchanged for future starts and updates.
4. Keep `ingress_enabled` on for Home Assistant browser access. Set `ingress_origins` to a list of the Home Assistant origins you use, including each scheme and port, without trailing slashes or paths. The default list contains `http://homeassistant.local:8123`; add your Cloud origin for remote access as described below.
5. Start the app. The first start initializes the database and administrator and can take several minutes. Follow startup progress in the app logs.

No companion apps, published web ports, certificate or public DNS name are required for the default setup.

## Open the dashboard through Home Assistant

Open **OpenWISP** from the Home Assistant sidebar or app page. In the app configuration, add an `ingress_users` entry for every Home Assistant user who should enter OpenWISP:

```yaml
ingress_users:
  - ha_user_id: YOUR_HOME_ASSISTANT_USER_ID
    openwisp_username: EXISTING_OPENWISP_USERNAME
```

Use the stable user ID from Home Assistant user settings, not the display name. Each mapped user must already have an active OpenWISP account. Their OpenWISP permissions still apply; the mapping does not create accounts or grant administrator access. Create or manage accounts by signing in with the configured OpenWISP administrator account.

For local and remote access, list both Home Assistant origins. Replace the external example with the origin of your Home Assistant Cloud or external address; keep that address private:

```yaml
ingress_origins:
  - http://homeassistant.local:8123
  - https://your-external-ha.example
```

Restart the app after changing origins. When ingress is enabled, `ingress_origins` must contain 1–16 entries. A single address also belongs in this list.

**Upgrading to 1.0.0:** `ingress_origin` has been removed. Move its address into `ingress_origins` if it is not already listed, and delete `ingress_origin` from the saved app configuration, even if its value is empty. The app rejects the removed field instead of ignoring a saved address. Preserve your user mappings and other configuration values. To downgrade to a version before 0.5.5, restore one address as `ingress_origin` and remove `ingress_origins` from the configuration.

Origins accept HTTP or HTTPS, DNS names, IPv4 and bracketed IPv6, with an optional port. Scheme and hostname case and default ports (80/443) are normalized. Paths (including a trailing slash), credentials, wildcards, trailing DNS dots, empty entries and duplicate origins are rejected. Each origin must have a different hostname: browsers share cookies across ports, and HTTP and HTTPS on the same hostname can interfere with one another's cookies. Use separate hostnames for those addresses.

HTTPS origins receive Secure cookies; HTTP origins receive cookies usable over HTTP. Both use host-only cookies scoped to the ingress session path. HTTP exposes the Home Assistant session and ingress traffic to the network; use HTTPS wherever possible, and only use HTTP on a trusted local network. Listing multiple origins does not permit form submissions or WebSockets across them. Open the panel normally from Home Assistant at the selected address so its iframe and WebSockets have the same origin.

## Optional direct HTTPS for devices and external clients

Use this only when network devices or API clients need to reach OpenWISP outside the browser ingress session.

1. Choose DNS names that resolve to the Home Assistant host, and obtain a PEM certificate chain covering both `dashboard_domain` and `api_domain`, plus its matching unencrypted private key. Put both files in Home Assistant's `ssl` directory.
2. Configure `direct_https: true`, both domain options, `certfile` and `keyfile`. Leave `external_url` empty to derive it from the API name and Network host port. If set, it must match that URL (omit port 443).
3. In the app's **Network** settings, set the host port for container `443/tcp`, for example `8443`. This is the only port setting; OpenWISP reads it from Supervisor at startup. Do not publish ingress port 8099.
4. Start or restart the app and use the HTTPS URL for OpenWISP and the agent API. Install the certificate's issuing CA on clients when it is private. Keep certificate verification enabled.

For a private certificate for `homeassistant.local`, the repository helper can create a local CA and server certificate using Python 3 and OpenSSL:

```sh
python3 tools/generate_local_certificate.py
```

It writes files outside the repository by default. Copy the server chain and private key into Home Assistant's `ssl` directory, keep the CA private key secure, and install the public CA certificate on each client that needs to trust it. The helper's `--help` describes `--output` and `--renew`. A certificate does not configure name resolution; clients must resolve the chosen names to the Home Assistant host.

To check the saved port mapping, run this read-only diagnostic from the repository root with SSH access and Python 3 available locally:

```sh
ssh HOME_ASSISTANT_HOST 'ha apps info APP_SLUG --raw-json' | python3 tools/interface_mapping_check.py
```

With Network host port `8443`, use `https://homeassistant.local:8443` when both domains are `homeassistant.local`. Container port 443 stays fixed. Direct HTTPS requires a valid Network mapping and Supervisor access; startup reports an error if either is unavailable.

**Upgrading to 1.0.1:** set the desired Network host port (for example `8443`), remove `https_port` from saved app options, and restart after installing this update. When changing ports later, change only Network and restart; update `external_url` too if you explicitly set it. Before retiring an endpoint, update enrolled devices and API clients that use it.

## Routine operation and persistent data

Use Home Assistant's app controls to start, stop and update OpenWISP, and the app log viewer to inspect startup or service errors. For a Home Assistant terminal, `ha apps logs openwisp` shows app logs. The app starts its internal database, cache, metrics service and web/worker processes in order; do not manage those services individually.

Persistent installation data lives in the app's `/data/openwisp/` directory. It includes the database, metrics history, cache snapshots, uploaded and private files, SSH keys, generated credentials and the Celery schedule. Collected static files can be regenerated. Keep the same app configuration secrets across updates. Uninstalling the app may delete its private data.

The app does not provide its own backup or restore command. PostgreSQL, metrics, files and credentials persist under the app data mount, but this is not a backup. Redis uses snapshots, so an abrupt Redis or host failure can lose changes since its latest snapshot. Arrange regular Home Assistant backups that include the OpenWISP app data. Keep a backup before updates, store a copy somewhere separate from the Home Assistant host, and verify that you can restore the app data and its configuration secrets together. Restore using a compatible app version; do not delete `/data/openwisp` to troubleshoot routine startup failures.

## Troubleshooting

### App will not start

Read the app log from the first reported error onward and confirm the required administrator values and `django_secret_key` are set. If direct HTTPS is enabled, confirm the certificate files exist in `ssl`, match, are current and cover both configured names. Correct the reported issue and start the whole app again. Failed initialization retains existing app data; do not reset it as a first recovery step.

### Home Assistant ingress denies access

Confirm `ingress_enabled` is true, the active Home Assistant origin is configured in `ingress_origins`, and the signed-in Home Assistant user ID maps to an active OpenWISP username. Confirm that account can sign in directly and has permission to access the requested OpenWISP objects.

If the iOS Companion app works locally but shows “Forbidden” over Cloud, check which connection it selected. Configure both the local and Cloud origins using the example above, restart OpenWISP, then reopen its panel through Home Assistant. Compare Safari and Companion at the same address and with the same Home Assistant user, then compare local and remote connections. Record whether opening, navigation or saving fails; a generic “Forbidden” does not identify which proxy or application rejected the request.

The ingress adapter logs fixed rejection categories such as `invalid browser origin`, `identity is not authorized`, `untrusted peer` and `duplicate ingress header`. It logs at most once per category per minute per process, without request values. Missing log entries can reflect rate limiting or an upstream rejection. Never share a full Cloud URL, ingress path, Home Assistant user ID, cookies, tokens or raw browser captures. Include exact app, Core, Supervisor, iOS and Companion versions when reporting the failure.

### Saving a form reports CSRF verification failed

Update the app and open it through Home Assistant with its active origin listed in `ingress_origins`, including scheme and port. Some Home Assistant versions send a `no-referrer` policy that made older app versions submit forms with `Origin: null`; current ingress admin pages specify a policy that preserves the origin without exposing the ingress path in referrers. Other CSRF failures can have different causes.

Check the app logs for the Django CSRF rejection reason; production debug mode is not needed. Before sharing a log excerpt, remove ingress paths, object IDs, hostnames, cookies, tokens and other private information. Never share a raw browser capture or copied request containing session information.

### Direct HTTPS fails

Check DNS resolution, certificate trust and name coverage, and the host port for `443/tcp` in Network. Restart after changing that mapping; OpenWISP uses it automatically. Keep the app's internal container port at 443. Review app logs for certificate validation errors without sharing private key contents.

### An agent cannot connect

Confirm that the agent can resolve and reach `api_domain`, uses the configured HTTPS port, trusts the certificate issuer, and has a valid OpenWISP device key and API configuration. Check network routing/firewall rules between the agent network and Home Assistant. Agent traffic does not use browser ingress.

### Startup fails after an update

Check the app logs and confirm the app configuration still contains the same Django secret. Start the app again after correcting a reported dependency or configuration issue. If state appears damaged, restore the app data and configuration secrets together from a known-good Home Assistant backup; do not remove individual database or credential files.

## Report an issue

Open an issue at [GitHub Issues](https://github.com/doozers-do/openwisp-ha/issues). Include the app version, Home Assistant installation type and architecture, the symptom, and relevant log lines after redacting hostnames or other private details as needed. Never post passwords, device keys, Django secrets, private keys or unredacted logs.

## Project references

- [Release history](openwisp/CHANGELOG.md)
- [Maintainer and development reference](docs/DEVELOPMENT.md)
- [MIT License](LICENSE)
