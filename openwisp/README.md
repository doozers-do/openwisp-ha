# OpenWISP

OpenWISP is a network configuration and monitoring controller packaged as one Home Assistant app. Install it on an amd64 Home Assistant OS or Supervisor based system. Configure an administrator username, email and password plus a Django secret, then start the app.

Open the app from the Home Assistant sidebar. Browser access uses Home Assistant ingress; map each permitted Home Assistant user ID to an existing OpenWISP account. The app keeps its database, metrics, uploaded files, keys and credentials in its private app data. Back up the app with Home Assistant backups.

See [DOCS.md](DOCS.md) for setup, optional direct HTTPS, operations and troubleshooting.
