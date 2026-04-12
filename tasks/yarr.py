"""Yarr: self-hosted RSS reader (native binary + SQLite)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import YARR

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

# --- Secrets ---
# Password must be alphanumeric (no colons) — it is embedded in "user:pass" auth format.

_creds = bw.yarr_creds()
_auth = f"{_creds['username']}:{_creds['password']}"

files.put(
    name="Write yarr.env",
    src=io.BytesIO(f"YARR_AUTH={_auth}\n".encode()),
    dest="/etc/secrets/yarr.env",
    user="root",
    group="root",
    mode="600",
)

# --- systemd service ---

service_unit = f"""\
[Unit]
Description=Yarr RSS reader
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/secrets/yarr.env
ExecStart=/usr/local/bin/yarr -addr {YARR["host"]}:{YARR["port"]} -db /var/lib/yarr/yarr.db -auth $YARR_AUTH
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
    name="Restart yarr if unit or env changed",
    commands=[
        f"""
        USTAMP=/etc/systemd/system/.yarr-unit-stamp
        ESTAMP=/etc/secrets/.yarr-env-stamp
        ENV_HASH=$(sha256sum /etc/secrets/yarr.env | cut -d' ' -f1)

        if [ "$(cat "$USTAMP" 2>/dev/null)" != "{_unit_hash}" ] || \
           [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
          systemctl restart yarr
          echo '{_unit_hash}' > "$USTAMP"
          echo "$ENV_HASH" > "$ESTAMP"
        fi
        """,
    ],
)
