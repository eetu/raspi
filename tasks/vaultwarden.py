"""Vaultwarden: self-hosted Bitwarden-compatible server (Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, VAULTWARDEN
from tasks.util import resolve_latest, restart_if_changed

_image = (
    resolve_latest("dani-garcia/vaultwarden", VAULTWARDEN["image"])
    if VAULTWARDEN.get("resolve_latest")
    else VAULTWARDEN["image"]
)

quadlet = f"""\
[Unit]
Description=Vaultwarden password manager
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=vaultwarden
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
MemoryMax=64M

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

# --- Data directory ---
# Secrets are written by tasks/secrets.py → /etc/secrets/vaultwarden.env
# Remove config.json so env vars are the sole source of truth. The admin panel
# writes this file and it overrides env vars for any key it contains.

files.file(
    name="Remove vaultwarden config.json (env vars are authoritative)",
    path="/var/lib/vaultwarden/config.json",
    present=False,
)

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
        restart_if_changed(
            "vaultwarden", _quadlet_hash, env_files=("/etc/secrets/vaultwarden.env",)
        )
    ],
)
