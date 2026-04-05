"""Diun: container image update notifier (Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import DIUN, NETWORK, NTFY

DOMAIN = NETWORK["domain"]
_dh = bw.dockerhub_creds()

diun_config = f"""\
watch:
  workers: 5
  schedule: "0 */6 * * *"

notif:
  ntfy:
    endpoint: "https://ntfy.{DOMAIN}"
    topic: "{NTFY["topic"]}"

regopts:
  - name: "index.docker.io"
    username: "{_dh["username"]}"
    password: "{_dh["password"]}"

defaults:
  watchRepo: true
  sortTags: semver
  maxTags: 10
  includeRepoTags:
    - "^v?\\d+\\.\\d+\\.\\d+$"

providers:
  docker:
    endpoint: "unix:///run/podman/podman.sock"
    watchByDefault: true
"""

quadlet = f"""\
[Unit]
Description=Diun image update notifier
After=network-online.target podman.socket
Wants=network-online.target

[Container]
Image={DIUN["image"]}
Network=host
Volume=/etc/diun/diun.yml:/etc/diun/diun.yml:ro
Volume=/run/podman/podman.sock:/run/podman/podman.sock:ro
Environment=LOG_LEVEL=info

[Service]
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""

_hash = hashlib.sha256((quadlet + diun_config).encode()).hexdigest()

files.directory(
    name="Create /etc/diun",
    path="/etc/diun",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write diun.yml",
    src=io.BytesIO(diun_config.encode()),
    dest="/etc/diun/diun.yml",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write diun.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/diun.container",
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

# Diun queries the Podman socket to discover running containers
systemd.service(
    name="Enable podman.socket",
    service="podman.socket",
    enabled=True,
    running=True,
    daemon_reload=True,
)

systemd.service(
    name="Start Diun",
    service="diun",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart Diun if config changed",
    commands=[
        f"""
        STAMP=/etc/diun/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_hash}" ]; then
          systemctl restart diun
          echo '{_hash}' > "$STAMP"
        fi
        """,
    ],
)
