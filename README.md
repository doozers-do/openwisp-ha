# OpenWISP for Home Assistant

An experimental Home Assistant app that runs the OpenWISP network configuration and monitoring controller, with its database, cache, metrics store, web server and workers in one app. It is intended for Home Assistant OS or another Supervisor based installation on **amd64** hardware. Home Assistant Container has no Supervisor app store and is outside the supported installation path.

## Install

In Home Assistant, add `https://github.com/doozers-do/openwisp-ha` as an app repository, install **OpenWISP**, configure the required administrator account and Django secret, then start it. Open the app from the Home Assistant sidebar. The [app guide](openwisp/DOCS.md) covers ingress access, optional HTTPS, operation, backups and troubleshooting.

This project is in alpha. Resource use and workload limits have not been measured across supported target hosts. Review known limitations and report problems through [GitHub Issues](https://github.com/doozers-do/openwisp-ha/issues). Do not include credentials, private keys or unredacted logs in a report.

- [App guide](openwisp/DOCS.md)
- [Release history](openwisp/CHANGELOG.md)
- [Maintainer and development reference](docs/DEVELOPMENT.md)

Licensed under [MIT License](LICENSE).
