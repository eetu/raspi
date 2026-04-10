"""Syncthing: continuous file synchronization (native binary)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import SYNCTHING

VERSION = SYNCTHING["version"]
BINARY_URL = (
    f"https://github.com/syncthing/syncthing/releases/download/{VERSION}/"
    f"syncthing-linux-arm64-{VERSION}.tar.gz"
)

_creds = bw.syncthing_creds()

# --- Binary ---

server.shell(
    name=f"Install Syncthing {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.syncthing-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" | tar -xz --strip-components=1 \
            -C /usr/local/bin "syncthing-linux-arm64-{VERSION}/syncthing"
          chmod +x /usr/local/bin/syncthing
          echo '{VERSION}' > "$STAMP"
        fi
        """,
    ],
)

# --- Data directory ---

files.directory(
    name="Create /var/lib/syncthing",
    path="/var/lib/syncthing",
    user="root",
    group="root",
    mode="700",
    present=True,
)

# --- Initial config with GUI credentials (first run only) ---

server.shell(
    name="Initialize Syncthing config",
    commands=[
        f"""
        if [ ! -f /var/lib/syncthing/config.xml ]; then
          syncthing generate \
            --config=/var/lib/syncthing \
            --data=/var/lib/syncthing \
            --gui-user={_creds["username"]!r} \
            --gui-password={_creds["password"]!r}
        fi
        """,
    ],
)

# --- systemd service ---

service_unit = f"""\
[Unit]
Description=Syncthing file synchronization
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/syncthing serve \
  --no-browser --no-restart \
  --config=/var/lib/syncthing \
  --data=/var/lib/syncthing \
  --gui-address=http://{SYNCTHING["host"]}:{SYNCTHING["port"]}
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=256M

[Install]
WantedBy=multi-user.target
"""

files.put(
    name="Write syncthing systemd unit",
    src=io.BytesIO(service_unit.encode()),
    dest="/etc/systemd/system/syncthing.service",
    user="root",
    group="root",
    mode="644",
)

_unit_hash = hashlib.sha256(service_unit.encode()).hexdigest()

systemd.service(
    name="Enable syncthing",
    service="syncthing",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart syncthing if unit changed",
    commands=[
        f"""
        STAMP=/etc/systemd/system/.syncthing-unit-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_unit_hash}" ]; then
          systemctl restart syncthing
          echo '{_unit_hash}' > "$STAMP"
        fi
        """,
    ],
)
