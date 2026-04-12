"""VuIO: DLNA media server for LAN streaming (native Rust binary)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import VUIO

VERSION = VUIO["version"]
BINARY_URL = f"https://github.com/vuiodev/vuio/releases/download/{VERSION}/vuio-linux-arm64.tar.gz"

# --- Binary ---

server.shell(
    name=f"Install vuio {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.vuio-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" -o /tmp/vuio.tar.gz
          tar -xzf /tmp/vuio.tar.gz -C /usr/local/bin/ vuio
          chmod +x /usr/local/bin/vuio
          rm /tmp/vuio.tar.gz
          echo '{VERSION}' > "$STAMP"
        fi
        """,
    ],
)

# --- Data directory ---

files.directory(
    name="Create /var/lib/vuio",
    path="/var/lib/vuio",
    user="root",
    group="root",
    mode="700",
    present=True,
)

# --- systemd service ---

service_unit = f"""\
[Unit]
Description=VuIO DLNA Media Server
After=network-online.target mnt-movies.automount
Wants=network-online.target mnt-movies.automount

[Service]
Type=simple
ExecStart=/usr/local/bin/vuio -p {VUIO["port"]} -n "Raspi" {VUIO["movies_path"]}
WorkingDirectory=/var/lib/vuio
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=64M
ProtectSystem=strict
ReadWritePaths=/var/lib/vuio
ReadOnlyPaths={VUIO["movies_path"]}
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
"""

files.put(
    name="Write vuio systemd unit",
    src=io.BytesIO(service_unit.encode()),
    dest="/etc/systemd/system/vuio.service",
    user="root",
    group="root",
    mode="644",
)

_unit_hash = hashlib.sha256(service_unit.encode()).hexdigest()

systemd.service(
    name="Enable vuio",
    service="vuio",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart vuio if unit changed",
    commands=[
        f"""
        STAMP=/etc/systemd/system/.vuio-unit-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_unit_hash}" ]; then
          systemctl restart vuio
          echo '{_unit_hash}' > "$STAMP"
        fi
        """,
    ],
)
