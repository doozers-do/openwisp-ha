# syntax=docker/dockerfile:1
FROM openwisp/openwisp-websocket:26.09.2@sha256:ea4b8024578f00a7e56733294cafac7b2737596e1049583878e35c775728fe26 AS inputs
USER root

# Reuse the exact upstream role files, without mixing Alpine executables into Debian.
ADD --checksum=sha256:254ee2fe065be84008fe8fb898770bfe88b59b6797a56680c9af95ca7c356ffb https://codeload.github.com/openwisp/docker-openwisp/tar.gz/7f1ebd58aa24f5104fdf9e1f532c948461cd1751 /tmp/upstream.tar.gz
ADD --checksum=sha256:084313d45052afdcdbb04da807c23c3fa6ed5f0ae3a2956ea70d60a14d1622ee https://dl.influxdata.com/influxdb/releases/influxdb-1.8.10-static_linux_amd64.tar.gz /tmp/influxdb.tar.gz
RUN mkdir -p /opt/inputs/upstream /opt/inputs/bin /tmp/upstream /tmp/influxdb \
    && tar -xzf /tmp/upstream.tar.gz --strip-components=1 -C /tmp/upstream \
    && cp -a /tmp/upstream/images/openwisp_dashboard /opt/inputs/upstream/dashboard \
    && cp -a /tmp/upstream/images/openwisp_api /opt/inputs/upstream/api \
    && cp -a /tmp/upstream/images/openwisp_nginx /opt/inputs/upstream/nginx \
    && cp /tmp/upstream/LICENSE /opt/inputs/upstream/LICENSE \
    && tar -xzf /tmp/influxdb.tar.gz -C /tmp/influxdb \
    && find /tmp/influxdb -type f \( -name influxd -o -name influx \) -exec cp '{}' /opt/inputs/bin/ \;

FROM openwisp/openwisp-websocket:26.09.2@sha256:ea4b8024578f00a7e56733294cafac7b2737596e1049583878e35c775728fe26

USER root
ARG REDIS_PACKAGE_VERSION=5:8.0.2-3+deb13u3
ARG POSTGRESQL_PACKAGE_VERSION=17.11-0+deb13u1
ARG POSTGIS_PACKAGE_VERSION=3.5.2+dfsg-1
ARG NGINX_PACKAGE_VERSION=1.26.3-3+deb13u10
ARG BROTLI_PACKAGE_VERSION=1.0.0~rc-6
RUN printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d \
    && chmod 755 /usr/sbin/policy-rc.d \
    && mkdir -p /etc/postgresql-common \
    && printf 'create_main_cluster = false\n' > /etc/postgresql-common/createcluster.conf \
    && apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
        redis-server=${REDIS_PACKAGE_VERSION} redis-tools=${REDIS_PACKAGE_VERSION} \
        postgresql-17=${POSTGRESQL_PACKAGE_VERSION} \
        postgresql-client-17=${POSTGRESQL_PACKAGE_VERSION} \
        postgresql-17-postgis-3=${POSTGIS_PACKAGE_VERSION} \
        postgresql-17-postgis-3-scripts=${POSTGIS_PACKAGE_VERSION} \
        nginx=${NGINX_PACKAGE_VERSION} nginx-common=${NGINX_PACKAGE_VERSION} \
        libnginx-mod-http-brotli-static=${BROTLI_PACKAGE_VERSION} \
    && rm -rf /var/lib/apt/lists/* \
    && redis-server --version \
    && /usr/lib/postgresql/17/bin/postgres --version \
    && nginx -v \
    && python -c 'import supervisor, redis, daphne' \
    && useradd --system --user-group --no-create-home influxdb

COPY --from=inputs /opt/inputs/bin/ /usr/local/bin/
COPY --from=inputs /opt/inputs/upstream/ /opt/ha-openwisp/upstream/
COPY build-inputs.json /opt/ha-openwisp/build-inputs.json
COPY runtime/ /opt/ha-openwisp/
RUN python /opt/ha-openwisp/frontend.py \
    && cp -a /opt/openwisp /opt/ha-openwisp/dashboard \
    && cp /opt/ha-openwisp/upstream/dashboard/module_settings.py \
          /opt/ha-openwisp/upstream/dashboard/urls.py /opt/ha-openwisp/dashboard/openwisp/ \
    && cp /opt/ha-openwisp/upstream/dashboard/load_init_data.py \
          /opt/ha-openwisp/upstream/dashboard/openvpn.json /opt/ha-openwisp/dashboard/ \
    && cp -a /opt/openwisp /opt/ha-openwisp/api \
    && cp /opt/ha-openwisp/upstream/api/module_settings.py \
          /opt/ha-openwisp/upstream/api/urls.py /opt/ha-openwisp/api/openwisp/ \
    && cp /opt/ha-openwisp/sitecustomize.py /opt/ha-openwisp/api/ \
    && cp /opt/ha-openwisp/sitecustomize.py /opt/ha-openwisp/dashboard/ \
    && for role in /opt/openwisp /opt/ha-openwisp/dashboard /opt/ha-openwisp/api; do \
         cp /opt/ha-openwisp/host_settings.py "$role/openwisp/ha_host_settings.py" \
         && printf '\nfrom .ha_host_settings import *\n' >> "$role/openwisp/settings.py"; \
         for name in media private static; do \
           rm -rf "$role/$name" && ln -s "/data/openwisp/$name" "$role/$name"; \
         done; \
       done \
    && influxd version && test ! -d /var/lib/postgresql/17/main
ENTRYPOINT ["python", "-u", "/opt/ha-openwisp/run.py"]
CMD []
