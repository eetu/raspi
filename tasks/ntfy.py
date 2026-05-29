"""ntfy: self-hosted push notification server (Podman Quadlet).

Optional service — comment the NTFY dict in group_data/all.py to
retire it. The task then drops into a cleanup branch that stops +
disables the systemd unit and leaves /var/lib/ntfy (and the `ntfy`
BW item) untouched, so re-adding the block + redeploying restores
the service.

Note: ntfy is the alert sink for gatus, restic, trivy, and
network_monitor. Those tasks degrade gracefully (drop alerts) when
NTFY is retired — no other service breaks.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK
from tasks.util import optional

NTFY = optional("NTFY")


if NTFY is None:
    # Retired: keep state on disk, just stop + disable the unit so the
    # container exits and the port is freed.
    systemd.service(
        name="Stop + disable ntfy (kept on disk for rollback)",
        service="ntfy",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    server_yml = f"""\
base-url: "https://ntfy.{NETWORK["domain"]}"
listen-http: "{NTFY["host"]}:{NTFY["port"]}"
cache-file: /var/lib/ntfy/cache.db
auth-default-access: read-write
"""

    quadlet = f"""\
[Unit]
Description=ntfy notification server
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=ntfy
Image={NTFY["image"]}
Network=host
Volume=/etc/ntfy/server.yml:/etc/ntfy/server.yml:ro
Volume=/run/ntfy:/var/lib/ntfy
Exec=serve
AutoUpdate=registry

[Service]
Restart=always
RestartSec=10
RuntimeDirectory=ntfy
MemoryMax=64M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256((quadlet + server_yml).encode()).hexdigest()

    files.directory(
        name="Create /etc/ntfy",
        path="/etc/ntfy",
        user="root",
        group="root",
        mode="755",
        present=True,
    )

    files.put(
        name="Write ntfy server.yml",
        src=io.BytesIO(server_yml.encode()),
        dest="/etc/ntfy/server.yml",
        user="root",
        group="root",
        mode="644",
    )

    files.put(
        name="Write ntfy.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/ntfy.container",
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
        name="Start ntfy",
        service="ntfy",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart ntfy if config changed",
        commands=[
            f"""
        STAMP=/etc/ntfy/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart ntfy
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
        ],
    )
