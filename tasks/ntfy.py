"""ntfy: self-hosted push notification server (Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, NTFY

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
Image={NTFY["image"]}
Network=host
Volume=/etc/ntfy/server.yml:/etc/ntfy/server.yml:ro
Volume=/run/ntfy:/var/lib/ntfy
Exec=serve
AutoUpdate=registry
HealthCmd=CMD-SHELL nc -z 127.0.0.1 {NTFY["port"]}
HealthInterval=30s
HealthTimeout=5s
HealthRetries=3
HealthStartPeriod=15s

[Service]
Restart=always
RestartSec=10
RuntimeDirectory=ntfy

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
