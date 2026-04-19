"""Beszel: lightweight server monitoring (native binaries, hub + agent)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import BESZEL
from tasks.util import restart_if_changed

VERSION = BESZEL["version"]
_RELEASES = f"https://github.com/henrygd/beszel/releases/download/{VERSION}"
HUB_URL = f"{_RELEASES}/beszel_Linux_arm64.tar.gz"
AGENT_URL = f"{_RELEASES}/beszel-agent_Linux_arm64.tar.gz"

# --- Binaries ---

server.shell(
    name=f"Install beszel hub {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.beszel-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{HUB_URL}" | tar -xz -C /usr/local/bin beszel
          chmod +x /usr/local/bin/beszel
          echo '{VERSION}' > "$STAMP"
        fi
        """,
    ],
)

server.shell(
    name=f"Install beszel agent {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.beszel-agent-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{AGENT_URL}" | tar -xz -C /usr/local/bin beszel-agent
          chmod +x /usr/local/bin/beszel-agent
          echo '{VERSION}' > "$STAMP"
        fi
        """,
    ],
)

# --- Data dirs ---

for path in ("/var/lib/beszel-hub", "/var/lib/beszel-agent"):
    files.directory(
        name=f"Create {path}",
        path=path,
        user="root",
        group="root",
        mode="700",
        present=True,
    )

# --- Env files ---
# Hub: USER_EMAIL/USER_PASSWORD seed the hub UI user on first boot (write-once).
# `beszel superuser upsert` below keeps the PocketBase admin panel login in sync
# every deploy — hub UI password rotation still requires the web UI.
# Agent: KEY = hub ed25519 pubkey (shown when adding a system), TOKEN = agent
# auth token (hub /settings/tokens). On first deploy the Bitwarden item may not
# exist yet — hub/agent come up with empty creds; populate BW and redeploy.

_admin = bw.beszel_admin_creds()
_hub_env = f'USER_EMAIL="{_admin["email"]}"\nUSER_PASSWORD="{_admin["password"]}"\n'

_creds = bw.beszel_agent_creds()
_agent_env = f'KEY="{_creds["key"]}"\nTOKEN="{_creds["token"]}"\nLISTEN={BESZEL["agent_port"]}\n'

files.put(
    name="Write beszel-hub env",
    src=io.BytesIO(_hub_env.encode()),
    dest="/etc/secrets/beszel-hub.env",
    user="root",
    group="root",
    mode="600",
)

files.put(
    name="Write beszel-agent env",
    src=io.BytesIO(_agent_env.encode()),
    dest="/etc/secrets/beszel-agent.env",
    user="root",
    group="root",
    mode="600",
)

# --- systemd units ---

hub_unit = f"""\
[Unit]
Description=Beszel monitoring hub
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/var/lib/beszel-hub
EnvironmentFile=/etc/secrets/beszel-hub.env
ExecStart=/usr/local/bin/beszel serve --http {BESZEL["host"]}:{BESZEL["port"]}
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=64M
ProtectSystem=strict
ReadWritePaths=/var/lib/beszel-hub
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

agent_unit = """\
[Unit]
Description=Beszel monitoring agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/var/lib/beszel-agent
EnvironmentFile=/etc/secrets/beszel-agent.env
ExecStart=/usr/local/bin/beszel-agent
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=32M
ProtectSystem=strict
ReadWritePaths=/var/lib/beszel-agent
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
    name="Write beszel-hub systemd unit",
    src=io.BytesIO(hub_unit.encode()),
    dest="/etc/systemd/system/beszel-hub.service",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write beszel-agent systemd unit",
    src=io.BytesIO(agent_unit.encode()),
    dest="/etc/systemd/system/beszel-agent.service",
    user="root",
    group="root",
    mode="644",
)

_hub_hash = hashlib.sha256(hub_unit.encode()).hexdigest()
_agent_hash = hashlib.sha256(agent_unit.encode()).hexdigest()

systemd.service(
    name="Enable beszel-hub",
    service="beszel-hub",
    enabled=True,
    running=True,
    daemon_reload=True,
)

systemd.service(
    name="Enable beszel-agent",
    service="beszel-agent",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart beszel-hub if unit or env changed",
    commands=[
        restart_if_changed("beszel-hub", _hub_hash, env_files=("/etc/secrets/beszel-hub.env",))
    ],
)

server.shell(
    name="Restart beszel-agent if unit or env changed",
    commands=[
        restart_if_changed(
            "beszel-agent", _agent_hash, env_files=("/etc/secrets/beszel-agent.env",)
        )
    ],
)

# Idempotent admin sync: upsert PocketBase superuser from the hub env file each
# deploy. Hub UI user is only seeded on first boot (write-once) — rotate that
# via the web UI. No-op if USER_EMAIL/USER_PASSWORD are empty.
server.shell(
    name="Sync beszel superuser from Bitwarden",
    commands=[
        """
        set -a
        . /etc/secrets/beszel-hub.env
        set +a
        if [ -n "$USER_EMAIL" ] && [ -n "$USER_PASSWORD" ]; then
          cd /var/lib/beszel-hub
          /usr/local/bin/beszel superuser upsert "$USER_EMAIL" "$USER_PASSWORD"
        fi
        """,
    ],
)
