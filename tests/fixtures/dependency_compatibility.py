"""Exercise all selected dependency builds inside the actual, offline app image.

Run by image_lifecycle_check.py. Extra services and their data are disposable and use
separate ports alongside the app's normal persistent services.
"""

from contextlib import ExitStack
import importlib.metadata
import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import signal
import socket
from string import Template
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

PACKAGE = Path("/opt/ha-openwisp")
POSTGRES = "/usr/lib/postgresql/17/bin/"


def run(*args, **kwargs):
    result = subprocess.run(args, text=True, capture_output=True, timeout=300, **kwargs)
    assert result.returncode == 0, f"{args[0]} failed:\n{result.stdout}\n{result.stderr}"
    return result.stdout


def identity(user):
    account = pwd.getpwnam(user)
    return {"user": account.pw_uid, "group": account.pw_gid, "extra_groups": []}


def directory(path, user):
    path.mkdir(mode=0o700)
    account = pwd.getpwnam(user)
    os.chown(path, account.pw_uid, account.pw_gid)
    return path


def wait_port(port, process):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, f"Service for port {port} exited early"
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise AssertionError(f"Service for port {port} was not ready within 30s")


def start(stack, root, label, command, user, **kwargs):
    log = stack.enter_context(open(root / f"{label}.log", "w+"))
    process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                               start_new_session=True, **identity(user), **kwargs)

    def stop():
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, 9)
                process.wait(timeout=5)
                raise AssertionError(f"{label} required forced termination")
        assert process.returncode == 0, f"{label} did not exit cleanly: {process.returncode}"

    stack.callback(stop)
    return process


def pins():
    inputs = json.loads((PACKAGE / "build-inputs.json").read_text())
    for name, expected in inputs["debian_packages"].items():
        actual = run("dpkg-query", "-W", "-f=${Version}", name)
        assert actual == expected, f"{name}: expected {expected}, got {actual}"
    for name, expected in inputs["base_python_packages"].items():
        actual = importlib.metadata.version(name)
        assert actual == expected, f"{name}: expected {expected}, got {actual}"
    assert "1.8.10" in run("influxd", "version")
    assert not Path("/var/lib/postgresql/17/main").exists(), "Database state baked into image"


def django_checks():
    sys.path.insert(0, os.getcwd())
    sys.path.insert(0, str(PACKAGE))
    from service import configure_suffixes
    configure_suffixes()
    import django
    django.setup()
    from django.contrib.gis.db import models
    from django.contrib.gis.geos import Point
    from django.core.cache import cache
    from django.core.management import call_command
    from django.db import connection
    from openwisp.celery import app
    from openwisp_monitoring.db.backends.influxdb.client import DatabaseClient

    # Run the real controller + monitoring migrations against the selected builds.
    call_command("migrate", interactive=False, verbosity=0)
    call_command("migrate", interactive=False, verbosity=0)
    with connection.cursor() as cursor:
        cursor.execute("SHOW server_version")
        assert cursor.fetchone()[0].startswith("17.11")
        cursor.execute("SELECT PostGIS_Lib_Version()")
        assert cursor.fetchone()[0] == "3.5.2"

    class PackagingPoint(models.Model):
        position = models.PointField(srid=4326)

        class Meta:
            app_label = "geo"
            db_table = "dependency_spatial_compatibility"

    with connection.schema_editor() as editor:
        editor.create_model(PackagingPoint)
    point = Point(12.5, 41.9, srid=4326)
    saved = PackagingPoint.objects.create(position=point)
    assert PackagingPoint.objects.get(pk=saved.pk).position.equals(point)
    assert PackagingPoint.objects.filter(position__equals=point).count() == 1

    metrics = DatabaseClient()
    metrics.create_database()
    metrics.create_or_alter_retention_policy("compatibility", "1h")
    metrics.write("dependency_compatibility", {"value": 42.5}, tags={"device": "fixture"},
                  retention_policy="compatibility")
    values = metrics.read("dependency_compatibility", ["value"], {"device": "fixture"},
                          retention_policy="compatibility")
    assert len(values) == 1 and values[0]["value"] == 42.5, values

    cache.delete("ha-compatibility-worker")
    app.send_task("compatibility.probe", args=["delivered"], queue="ha-compatibility")
    deadline = time.monotonic() + 30
    while cache.get("ha-compatibility-worker") != "delivered":
        assert time.monotonic() < deadline, "Celery did not consume the Redis-broker task"
        time.sleep(0.1)
    # Exercise the upstream beat configuration and its on-disk scheduler format.
    from celery.beat import PersistentScheduler
    scheduler = PersistentScheduler(app=app, schedule_filename="compatibility-beat")
    assert "run_checks" in scheduler.schedule, "Monitoring schedule missing"
    scheduler.close()
    assert list(Path.cwd().glob("compatibility-beat*")), "Beat did not persist its schedule"
    call_command("collectstatic", interactive=False, verbosity=0)
    connection.close()
    print("PASS: repeated OpenWISP migrations; GeoDjango spatial roundtrip; "
          "monitoring InfluxDB write/read; Celery over Redis; beat state; static collection", flush=True)


def compatibility(root):
    sys.path.insert(0, str(PACKAGE))
    from run import environment
    credentials = json.loads(Path("/data/openwisp/secrets.json").read_text())
    env = environment(credentials, 30)
    password = secrets.token_hex(32)
    env.update({"DB_HOST": "127.0.0.1", "DB_PORT": "15432",
                "DB_PASS": password, "INFLUXDB_PORT": "18086",
                "INFLUXDB_USER": "compatibility", "INFLUXDB_PASS": password,
                "DJANGO_SETTINGS_MODULE": "openwisp.settings", "MODULE_NAME": "dashboard"})
    # Overlay the pinned dashboard role files in disposable application storage.
    # This leaves the running foundation's WebSocket settings untouched.
    dashboard = root / "dashboard"
    shutil.copytree("/opt/openwisp", dashboard, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("module_settings.py", "urls.py"):
        shutil.copyfile(PACKAGE / "upstream/dashboard" / name, dashboard / "openwisp" / name)
    (dashboard / "compatibility_tasks.py").write_text(
        'from openwisp.celery import app\nfrom django.core.cache import cache\n'
        '@app.task(name="compatibility.probe")\ndef deliver(value):\n'
        '    cache.set("ha-compatibility-worker", value, timeout=60)\n'
    )
    # Every Django entrypoint uses the same offline suffix behavior as the package.
    (dashboard / "sitecustomize.py").write_text(
        'import tldextract\ntldextract.extract = '
        'tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)\n'
    )
    account = pwd.getpwnam("openwisp")
    for path in (dashboard, *dashboard.rglob("*")):
        os.chown(path, account.pw_uid, account.pw_gid)
    env["PYTHONPATH"] = str(dashboard)

    pgdata = directory(root / "postgres", "postgres")
    pgsocket = directory(root / "pgsocket", "postgres")
    pwfile = root / "pgpassword"
    pwfile.write_text(password)
    pwfile.chmod(0o600)
    os.chown(pwfile, pwd.getpwnam("postgres").pw_uid, pwd.getpwnam("postgres").pw_gid)
    run(POSTGRES + "initdb", "-D", str(pgdata), "--username=admin", "--pwfile=" + str(pwfile),
        "--auth-host=scram-sha-256", "--auth-local=trust", **identity("postgres"))
    influxdata = directory(root / "influxdb", "influxdb")
    influxconfig = root / "influxdb.conf"
    influxconfig.write_text(
        f'bind-address = "127.0.0.1:18088"\nreporting-disabled = true\n'
        f'[meta]\n  dir = "{influxdata}/meta"\n'
        f'[data]\n  dir = "{influxdata}/data"\n  wal-dir = "{influxdata}/wal"\n'
        '[http]\n  bind-address = "127.0.0.1:18086"\n  auth-enabled = true\n'
        '  log-enabled = false\n[monitor]\n  store-enabled = false\n'
    )
    with ExitStack() as stack:
        pg = start(stack, root, "postgres", [POSTGRES + "postgres", "-D", str(pgdata),
                   "-k", str(pgsocket), "-h", "127.0.0.1", "-p", "15432",
                   "-c", "shared_buffers=16MB", "-c", "max_connections=20"], "postgres")
        wait_port(15432, pg)
        run(POSTGRES + "createdb", "-h", str(pgsocket), "-p", "15432", "-U", "admin",
            "openwisp_db", **identity("postgres"))
        influx = start(stack, root, "influxdb", ["influxd", "-config", str(influxconfig)], "influxdb")
        wait_port(18086, influx)
        from influxdb import InfluxDBClient
        client = InfluxDBClient("127.0.0.1", 18086, timeout=3)
        assert client.ping() == "1.8.10"
        client.create_user("compatibility", password, admin=True)
        try:
            client.get_list_database()
        except Exception as error:
            from influxdb.exceptions import InfluxDBClientError
            assert isinstance(error, InfluxDBClientError) and error.code == 401
        else:
            raise AssertionError("InfluxDB accepted an unauthenticated query")
        worker = start(stack, root, "celery", ["celery", "-A", "openwisp", "worker",
                       "--pool=solo", "--concurrency=1", "--queues=ha-compatibility", "--include=compatibility_tasks",
                       "--without-gossip", "--without-mingle", "--without-heartbeat", "--loglevel=WARNING"],
                       "openwisp", cwd=dashboard, env=env)
        checked = run("python", str(Path(__file__).resolve()), "django", cwd=dashboard,
                      env=env, **identity("openwisp"))
        print(checked, end="", flush=True)
        assert worker.poll() is None, "Celery exited during dependency checks"
        uwsgi = start(stack, root, "uwsgi", ["uwsgi", "--ini", str(PACKAGE / "uwsgi.ini"),
                      "--chdir", str(dashboard), "--socket=127.0.0.1:18000"],
                      "openwisp", env=env)
        wait_port(18000, uwsgi)
        proxy_check(stack, root)
        assert all(process.poll() is None for process in (pg, influx, worker, uwsgi))


def proxy_check(stack, root):
    nginxdir = directory(root / "nginx", "www-data")
    public = directory(root / "public", "www-data")
    (public / "static").symlink_to(root / "dashboard/static", target_is_directory=True)
    template = (PACKAGE / "upstream/nginx/openwisp.template.conf").read_text()
    server = Template(template).substitute({
        "DOLLAR": "$", "NGINX_IP6_80_STRING": "", "DOMAIN": "127.0.0.1",
        "NGINX_GZIP_SWITCH": "on", "NGINX_GZIP_LEVEL": "6", "NGINX_GZIP_PROXIED": "any",
        "NGINX_GZIP_MIN_LENGTH": "1000", "NGINX_GZIP_TYPES": "text/plain text/css",
        "NGINX_80_CONFIG": "", "NGINX_BROTLI_SWITCH": "on", "NGINX_HTTPS_ALLOWED_IPS": "all",
        "NGINX_SECURITY_HEADERS_FILE": str(PACKAGE / "upstream/nginx/openwisp.security.http.conf"),
        "WEBSOCKET_APP_SERVICE": "127.0.0.1", "WEBSOCKET_APP_PORT": "8002",
        "APP_SERVICE": "127.0.0.1", "APP_PORT": "18000",
    }).replace("listen 80;", "listen 127.0.0.1:18080;")
    server = server.replace("/dev/stdout", str(nginxdir / "access.log"))
    server = server.replace("include uwsgi_params;", "include /etc/nginx/uwsgi_params;")
    server = server.replace("/opt/openwisp/public", str(public))
    conf = nginxdir / "nginx.conf"
    conf.write_text(
        'load_module /usr/lib/nginx/modules/ngx_http_brotli_static_module.so;\n'
        f'pid {nginxdir}/nginx.pid;\nerror_log {nginxdir}/error.log;\n'
        'events { worker_connections 64; }\nhttp {\n'
        'include /etc/nginx/mime.types;\ninclude /etc/nginx/uwsgi_params;\n'
        'log_format main "$status";\n'
        f'client_body_temp_path {nginxdir}/body;\nuwsgi_temp_path {nginxdir}/uwsgi;\n'
        f'proxy_temp_path {nginxdir}/proxy;\nfastcgi_temp_path {nginxdir}/fastcgi;\n'
        f'scgi_temp_path {nginxdir}/scgi;\n{server}\n}}\n'
    )
    run("nginx", "-t", "-p", "/etc/nginx/", "-c", str(conf), **identity("www-data"))
    proxy = start(stack, root, "nginx", ["nginx", "-p", "/etc/nginx/", "-c", str(conf),
                  "-g", "daemon off;"], "www-data")
    wait_port(18080, proxy)
    with urllib.request.urlopen("http://127.0.0.1:18080/admin/login/", timeout=15) as response:
        assert response.status == 200 and b"csrfmiddlewaretoken" in response.read(), "Dashboard WSGI failed"
    manifest = json.loads((root / "dashboard/static/staticfiles.json").read_text())
    asset = manifest["paths"]["admin/css/base.css"]
    request = urllib.request.Request("http://127.0.0.1:18080/static/" + asset,
                                     headers={"Accept-Encoding": "br"})
    with urllib.request.urlopen(request, timeout=5) as response:
        assert response.headers.get("Content-Encoding") == "br", "Brotli static module not serving assets"
        assert response.read() == (root / "dashboard/static" / (asset + ".br")).read_bytes()
    try:
        urllib.request.urlopen("http://127.0.0.1:18080/ws/unmatched-route", timeout=5)
    except urllib.error.HTTPError as error:
        assert error.code == 404, f"Upstream ASGI proxy returned {error.code}"
    else:
        raise AssertionError("Unexpected ASGI route")
    print("PASS: native Nginx/Brotli serves compressed upstream assets; real dashboard over uWSGI; "
          "ASGI reverse proxy; all dependency processes run unprivileged", flush=True)


def main():
    pins()
    with tempfile.TemporaryDirectory(prefix="ha-dependency-compatibility-") as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        try:
            compatibility(root)
        except Exception:
            for logfile in root.rglob("*.log"):
                print(f"--- {logfile.name} ---\n{logfile.read_text()[-12000:]}", file=sys.stderr)
            raise


if __name__ == "__main__":
    if sys.argv[1:] == ["django"]:
        django_checks()
    else:
        main()
