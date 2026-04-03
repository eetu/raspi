"""HCC: Podman Quadlet container unit."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import HCC

_env_hash = hashlib.sha256(bw.hcc_env().encode()).hexdigest()

quadlet = f"""\
[Unit]
Description=HCC Dashboard
After=network-online.target
Wants=network-online.target

[Container]
Image={HCC["image"]}
Network=host
Environment=PORT={HCC["port"]}
Environment=HOSTNAME={HCC["host"]}
EnvironmentFile=/etc/secrets/hcc.env
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

files.directory(
    name="Create /etc/containers/systemd",
    path="/etc/containers/systemd",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write hcc.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/hcc.container",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Reload quadlet units",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true"
    ],
)

systemd.service(
    name="Start HCC",
    service="hcc",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart HCC if quadlet changed",
    commands=[
        f"""
        STAMP=/etc/containers/systemd/.hcc-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart hcc
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)

server.shell(
    name="Restart HCC if env changed",
    commands=[
        f"""
        STAMP=/etc/secrets/.hcc-env-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_env_hash}" ]; then
          systemctl restart hcc
          echo '{_env_hash}' > "$STAMP"
        fi
        """,
    ],
)
