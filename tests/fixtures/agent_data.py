"""Disposable OpenWISP test identity, template, and cleanup for agent workflow checks."""
import json
import os
import secrets
import sys
import uuid
from pathlib import Path


def inherit_supervisor_environment():
    # Supervisor holds the validated, translated upstream environment. Reuse it
    # so this local fixture runs with the same Django settings as the app roles.
    for entry in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = entry.read_bytes().split(b"\0")
            if any(b"supervisord" in arg for arg in args):
                environ = entry.parent / "environ"
                for item in environ.read_bytes().split(b"\0"):
                    if b"=" in item:
                        key, value = item.split(b"=", 1)
                        os.environ[key.decode()] = value.decode()
                return
        except (OSError, UnicodeDecodeError):
            continue
    raise RuntimeError("could not find the OpenWISP supervisor environment")


inherit_supervisor_environment()

sys.path.insert(0, "/opt/ha-openwisp/dashboard")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "openwisp.settings")
import django

django.setup()

from swapper import load_model
from django.core.cache import cache

Organization = load_model("openwisp_users", "Organization")
Device = load_model("config", "Device")
Config = load_model("config", "Config")
Template = load_model("config", "Template")


def setup():
    token = uuid.uuid4().hex[:12]
    organization = Organization.objects.create(
        name=f"agent smoke {token}", slug=f"agent-smoke-{token}", is_active=True
    )
    template = Template.objects.create(
        name=f"agent smoke {token}",
        organization=organization,
        backend="netjsonconfig.OpenWrt",
        config={"interfaces": [{"name": f"p5b{token[:5]}", "type": "ethernet"}]},
    )
    device = Device.objects.create(
        name=f"agent-agent-{token}",
        organization=organization,
        mac_address="02:%s:%s:%s:%s:%s" % tuple(token[i:i + 2] for i in (0, 2, 4, 6, 8)),
        hardware_id=uuid.uuid4().hex,
        model="agent disposable fixture",
        os="OpenWrt-compatible fixture",
        key=secrets.token_urlsafe(24),
    )
    config = Config.objects.create(
        device=device,
        backend="netjsonconfig.OpenWrt",
        config={"general": {}},
    )
    config.templates.add(template)
    cache.clear()
    template_ids = list(config.templates.values_list("pk", flat=True))
    if template.pk not in template_ids:
        raise RuntimeError("smoke template did not attach to the device configuration")
    result = {
        "organization_id": str(organization.pk),
        "template_id": str(template.pk),
        "device_id": str(device.pk),
        "device_name": device.name,
        "device_key": device.key,
        "token": token,
        "before": f"p5b{token[:5]}",
        "after": f"p5a{token[:5]}",
    }
    print(json.dumps(result))


def update(template_id, after):
    template = Template.objects.get(pk=template_id)
    template.config = {"interfaces": [{"name": after, "type": "ethernet"}]}
    template.save()
    cache.clear()


def cleanup(organization_id):
    Organization.objects.filter(pk=organization_id).delete()
    cache.clear()


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "setup":
        setup()
    elif action == "update":
        update(sys.argv[2], sys.argv[3])
    elif action == "cleanup":
        cleanup(sys.argv[2])
    else:
        raise SystemExit("expected setup, update, or cleanup")
