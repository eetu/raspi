"""Vaultwarden: self-hosted Bitwarden-compatible server (Podman Quadlet)."""

import hashlib
import io
import json
import re
import urllib.request

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import NETWORK, VAULTWARDEN


def _latest_vaultwarden_tag() -> str:
    """Query GitHub releases for the latest tag matching the current major version."""
    major = VAULTWARDEN["image"].split(":")[-1].split(".")[0]
    pattern = re.compile(rf"^{re.escape(major)}\.\d+\.\d+$")

    req = urllib.request.Request(
        "https://api.github.com/repos/dani-garcia/vaultwarden/releases?per_page=5",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req) as r:
        releases = json.loads(r.read())

    matching = [
        r["tag_name"].lstrip("v") for r in releases if pattern.match(r["tag_name"].lstrip("v"))
    ]
    if not matching:
        raise RuntimeError(f"No vaultwarden releases found for major version {major}")
    return matching[0]


_image = (
    f"docker.io/vaultwarden/server:{_latest_vaultwarden_tag()}"
    if VAULTWARDEN.get("resolve_latest")
    else VAULTWARDEN["image"]
)

quadlet = f"""\
[Unit]
Description=Vaultwarden password manager
After=network-online.target
Wants=network-online.target

[Container]
Image={_image}
Network=host
Volume=/var/lib/vaultwarden:/data
Environment=DOMAIN=https://vault.{NETWORK["domain"]}
Environment=ROCKET_ADDRESS={VAULTWARDEN["host"]}
Environment=ROCKET_PORT={VAULTWARDEN["port"]}
EnvironmentFile=/etc/secrets/vaultwarden.env

[Service]
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

# --- Secrets ---

files.put(
    name="Write vaultwarden.env",
    src=io.BytesIO(
        (
            f"ADMIN_TOKEN={bw.vaultwarden_admin_token_hash()}\n"
            f"SIGNUPS_ALLOWED=false\n"
            f"SMTP_HOST=smtp.gmail.com\n"
            f"SMTP_PORT=587\n"
            f"SMTP_SECURITY=starttls\n"
            f"SMTP_USERNAME=huecontrolcenter@gmail.com\n"
            f"SMTP_FROM=huecontrolcenter@gmail.com\n"
            f"SMTP_PASSWORD={bw.vaultwarden_smtp_password()}\n"
        ).encode()
    ),
    dest="/etc/secrets/vaultwarden.env",
    user="root",
    group="root",
    mode="600",
)

# --- Data directory ---

files.directory(
    name="Create /var/lib/vaultwarden",
    path="/var/lib/vaultwarden",
    user="root",
    group="root",
    mode="700",
    present=True,
)

# --- Quadlet ---

files.put(
    name="Write vaultwarden.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/vaultwarden.container",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Reload quadlet units",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true",
    ],
)

systemd.service(
    name="Start vaultwarden",
    service="vaultwarden",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart vaultwarden if quadlet or env changed",
    commands=[
        f"""
        QSTAMP=/etc/containers/systemd/.vaultwarden-quadlet-stamp
        ESTAMP=/etc/secrets/.vaultwarden-env-stamp
        ENV_HASH=$(sha256sum /etc/secrets/vaultwarden.env | cut -d' ' -f1)

        if [ "$(cat "$QSTAMP" 2>/dev/null)" != "{_quadlet_hash}" ] || \
           [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
          systemctl restart vaultwarden
          echo '{_quadlet_hash}' > "$QSTAMP"
          echo "$ENV_HASH" > "$ESTAMP"
        fi
        """,
    ],
)
