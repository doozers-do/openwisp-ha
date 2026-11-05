"""Assert the normal app's initialization, private listeners and durable state."""

import hashlib
import json
import os
from pathlib import Path
import pwd
import stat
import subprocess
import sys

import psycopg
from influxdb import InfluxDBClient
from influxdb.exceptions import InfluxDBClientError

sys.path.insert(0, "/opt/ha-openwisp")
from run import read_options
from settings import upstream_environment
from service import configure_suffixes


def main():
    state = Path("/data/openwisp")
    credentials = json.loads((state / "secrets.json").read_text())
    options = read_options()
    os.environ.update(upstream_environment(os.environ, options, credentials))
    os.environ.update(MODULE_NAME="dashboard", DJANGO_SETTINGS_MODULE="openwisp.settings")
    sys.path.insert(0, "/opt/ha-openwisp/dashboard")
    configure_suffixes()
    import django
    django.setup()
    from django.contrib.auth import get_user_model
    from django.db import connection
    from django.db.migrations.executor import MigrationExecutor
    from openwisp_monitoring.db.backends.influxdb.client import DatabaseClient
    from swapper import load_model

    assert Path("/run/ha-openwisp/initialization/complete").is_file()
    executor = MigrationExecutor(connection)
    assert not executor.migration_plan(executor.loader.graph.leaf_nodes()), "Migrations incomplete"
    User = get_user_model()
    user = User.objects.get(username=options["admin_username"], is_superuser=True)
    assert not User.objects.filter(username="admin").exists(), "Upstream default account was created"
    Template = load_model("config", "Template")
    Credentials = load_model("connection", "Credentials")
    assert Template.objects.count() == Credentials.objects.count() == 1
    ssh_template = Template.objects.get(name="SSH Keys")
    ssh_credentials = Credentials.objects.get(name="OpenWISP Default")
    assert ssh_credentials.params["key"] == (state / "ssh/id_ed25519").read_text()
    assert ssh_template.config["files"][0]["contents"] == (state / "ssh/id_ed25519.pub").read_text()

    write = sys.argv[1] == "write"
    if write:
        assert user.check_password(options["admin_password"])
        user.set_password("disposable-changed-password")
        user.save()
    else:
        assert user.check_password("disposable-changed-password"), "Startup reset the administrator"
    with connection.cursor() as cursor:
        cursor.execute("SELECT PostGIS_Lib_Version()")
        assert cursor.fetchone()[0] == "3.5.2"
        if write:
            cursor.execute("CREATE TABLE service_checkpoint (value text, position geometry(Point,4326))")
            cursor.execute("INSERT INTO service_checkpoint VALUES (%s, ST_SetSRID(ST_Point(12.5,41.9),4326))",
                           ["durable-record"])
        cursor.execute("SELECT value, ST_X(position), ST_Y(position) FROM service_checkpoint")
        assert cursor.fetchall() == [("durable-record", 12.5, 41.9)]
    metrics = DatabaseClient()
    if write:
        metrics.create_or_alter_retention_policy("service_persistence", "1h")
        metrics.write("service_persistence", {"value": 42.5}, tags={"device": "fixture"},
                      retention_policy="service_persistence")
    samples = metrics.read("service_persistence", ["value"], {"device": "fixture"}, retention_policy="service_persistence")
    assert len(samples) == 1 and samples[0]["value"] == 42.5

    for name, owner, mode in (
        ("postgres", "postgres", 0o700), ("influxdb", "influxdb", 0o700),
        ("redis", "redis", 0o700), ("ssh", "openwisp", 0o700),
        ("media", "openwisp", 0o2750), ("private", "openwisp", 0o700),
        ("static", "openwisp", 0o755), ("celery", "openwisp", 0o700),
    ):
        info = (state / name).stat()
        assert info.st_uid == pwd.getpwnam(owner).pw_uid, f"{name} owner"
        assert stat.S_IMODE(info.st_mode) == mode, f"{name} permissions"
    for name in ("media", "private", "celery"):
        path = state / name / "service-persistence"
        if write:
            path.write_text("durable-file")
            account = pwd.getpwnam("openwisp")
            os.chown(path, account.pw_uid, account.pw_gid)
        assert path.read_text() == "durable-file"
    assert (state / "static/staticfiles.json").is_file()
    manifest = json.loads((state / "static/staticfiles.json").read_text())
    asset = state / "static" / (manifest["paths"]["admin/css/base.css"] + ".br")
    nginx = pwd.getpwnam("www-data")
    readable = subprocess.run([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).read_bytes()",
                               str(asset)], user=nginx.pw_uid, group=nginx.pw_gid, extra_groups=[],
                              capture_output=True, timeout=10)
    assert readable.returncode == 0, "Collected static assets cannot be read by Nginx"
    for role in (Path("/opt/openwisp"), Path("/opt/ha-openwisp/dashboard")):
        for name in ("media", "private", "static"):
            assert (role / name).resolve() == state / name
    # Verify actual socket bindings, including InfluxDB's RPC listener.
    listeners = {}
    for row in Path("/proc/net/tcp").read_text().splitlines()[1:]:
        columns = row.split()
        if columns[3] == "0A":
            host, port = columns[1].split(":")
            listeners[int(port, 16)] = host
    assert set(listeners) == {443, 8000, 8001, 5432, 6379, 8086, 8088, 8002}, listeners
    assert listeners.pop(443) == "00000000", "Nginx must serve HTTPS"
    assert all(host == "0100007F" for host in listeners.values()), "Non-loopback listener"
    ipv6_listeners = {}
    for row in Path("/proc/net/tcp6").read_text().splitlines()[1:]:
        columns = row.split()
        if columns[3] == "0A":
            host, port = columns[1].split(":")
            ipv6_listeners[int(port, 16)] = host
    assert ipv6_listeners == {443: "0" * 32}, ipv6_listeners
    try:
        psycopg.connect(host="127.0.0.1", user="admin", dbname="openwisp_db", connect_timeout=1)
    except psycopg.OperationalError:
        pass
    else:
        raise AssertionError("PostgreSQL accepted unauthenticated access")
    client = InfluxDBClient(host="127.0.0.1", timeout=1, retries=1)
    try:
        client.get_list_database()
    except InfluxDBClientError as error:
        assert error.code == 401
    else:
        raise AssertionError("InfluxDB accepted unauthenticated access")
    finally:
        client.close()
    durable = [state / "secrets.json", state / "ssh/id_ed25519", state / "ssh/id_ed25519.pub"]
    print(json.dumps({"hashes": [hashlib.sha256(path.read_bytes()).hexdigest() for path in durable],
                      "admin": str(user.pk), "template": str(ssh_template.pk),
                      "credentials": str(ssh_credentials.pk), "metric": samples[0]}))


if __name__ == "__main__":
    main()
