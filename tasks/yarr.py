"""Yarr: self-hosted RSS reader (native binary + SQLite).

Auth is delegated to oauth2-proxy via Traefik forward-auth — yarr's own basic
auth is intentionally disabled. The bind is 127.0.0.1, so Traefik is the only
ingress.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import YARR
from tasks.util import restart_if_changed

VERSION = YARR["version"]
BINARY_URL = f"https://github.com/nkanaev/yarr/releases/download/{VERSION}/yarr_linux_arm64.zip"

# --- Binary ---

server.shell(
    name=f"Install yarr {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.yarr-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" -o /tmp/yarr.zip
          unzip -o /tmp/yarr.zip yarr -d /usr/local/bin/
          chmod +x /usr/local/bin/yarr
          rm /tmp/yarr.zip
          echo '{VERSION}' > "$STAMP"
        fi
        """,
    ],
)

# --- Data directory ---

files.directory(
    name="Create /var/lib/yarr",
    path="/var/lib/yarr",
    user="root",
    group="root",
    mode="700",
    present=True,
)

# --- systemd service ---

service_unit = f"""\
[Unit]
Description=Yarr RSS reader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/yarr -addr {YARR["host"]}:{YARR["port"]} -db /var/lib/yarr/yarr.db
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=64M
ProtectSystem=strict
ReadWritePaths=/var/lib/yarr
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
    name="Write yarr systemd unit",
    src=io.BytesIO(service_unit.encode()),
    dest="/etc/systemd/system/yarr.service",
    user="root",
    group="root",
    mode="644",
)

_unit_hash = hashlib.sha256(service_unit.encode()).hexdigest()

systemd.service(
    name="Enable yarr",
    service="yarr",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart yarr if unit changed",
    commands=[restart_if_changed("yarr", _unit_hash)],
)

# Clean up the legacy yarr.env that previously held basic-auth credentials.
server.shell(
    name="Remove legacy yarr.env",
    commands=["rm -f /etc/secrets/yarr.env"],
)
