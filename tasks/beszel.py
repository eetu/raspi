"""Beszel: lightweight server monitoring (hub as native binary, agent as Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import BESZEL
from tasks.util import restart_if_changed

VERSION = BESZEL["version"]
_HUB_RELEASE = (
    f"https://github.com/henrygd/beszel/releases/download/{VERSION}/beszel_Linux_arm64.tar.gz"
)

# --- Hub binary ---

server.shell(
    name=f"Install beszel hub {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.beszel-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{_HUB_RELEASE}" | tar -xz -C /usr/local/bin beszel
          chmod +x /usr/local/bin/beszel
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
# Hub: USER_EMAIL/USER_PASSWORD seed the hub on first boot. Both the PocketBase
# superuser and the regular hub UI user are synced from BW on every deploy, so
# password rotation is: update BW → redeploy.
# Agent: TOKEN (universal) + KEY (hub ed25519 pubkey) are written on-Pi after
# the hub starts. TOKEN is only fetched once; KEY is synced on every deploy.

_admin = bw.beszel_admin_creds()
_hub_env = (
    f'USER_EMAIL="{_admin["email"]}"\nUSER_PASSWORD="{_admin["password"]}"\nUSER_CREATION=true\n'
)

files.put(
    name="Write beszel-hub env",
    src=io.BytesIO(_hub_env.encode()),
    dest="/etc/secrets/beszel-hub.env",
    user="root",
    group="root",
    mode="600",
)

server.shell(
    name="Initialize beszel-agent env if not present",
    commands=[
        """
        if [ ! -f /etc/secrets/beszel-agent.env ]; then
          printf 'TOKEN=\nKEY=\n' > /etc/secrets/beszel-agent.env
          chmod 600 /etc/secrets/beszel-agent.env
        fi
        """,
    ],
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

# Agent runs as a Podman Quadlet so it can monitor containers via the Podman
# socket. Network=host is required for accurate network interface statistics.
# Outbound mode: agent connects to hub (no SSH listener needed) and
# auto-registers the Pi system on first connect using the universal token.
agent_quadlet = f"""\
[Unit]
Description=Beszel monitoring agent
After=beszel-hub.service network-online.target
Wants=network-online.target

[Container]
ContainerName=beszel-agent
Image={BESZEL["agent_image"]}
Network=host
EnvironmentFile=/etc/secrets/beszel-agent.env
Environment=HUB_URL=http://{BESZEL["host"]}:{BESZEL["port"]}
Environment=DISABLE_SSH=true
Environment=DATA_DIR=/var/lib/beszel-agent
Volume=/var/lib/beszel-agent:/var/lib/beszel-agent
Volume=/run/podman/podman.sock:/var/run/docker.sock:ro

[Service]
Restart=always
RestartSec=5
MemoryMax=32M

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
    name="Write beszel-agent Quadlet",
    src=io.BytesIO(agent_quadlet.encode()),
    dest="/etc/containers/systemd/beszel-agent.container",
    user="root",
    group="root",
    mode="644",
)

_hub_hash = hashlib.sha256(hub_unit.encode()).hexdigest()
_agent_quadlet_hash = hashlib.sha256(agent_quadlet.encode()).hexdigest()

systemd.service(
    name="Enable beszel-hub",
    service="beszel-hub",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Reload Quadlet units",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true",
    ],
)

systemd.service(
    name="Start beszel-agent",
    service="beszel-agent",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart beszel-hub if unit or env changed",
    commands=[
        restart_if_changed("beszel-hub", _hub_hash, env_files=("/etc/secrets/beszel-hub.env",))
    ],
)

# Idempotent admin sync: upsert PocketBase superuser from the hub env file each
# deploy. No-op if USER_EMAIL/USER_PASSWORD are empty.
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

# Sync the regular hub user's password on every deploy so password rotation
# works by updating BW and redeploying. Uses superuser token to PATCH the
# user record. Credentials passed via env so special chars are safe.
server.shell(
    name="Sync beszel user password from Bitwarden",
    commands=[
        f"""
        set -a; . /etc/secrets/beszel-hub.env; set +a
        if [ -z "$USER_EMAIL" ] || [ -z "$USER_PASSWORD" ]; then exit 0; fi
        export __BZ_EMAIL="$USER_EMAIL" __BZ_PW="$USER_PASSWORD"
        python3 << 'PYEOF'
import json, os, urllib.request
email = os.environ["__BZ_EMAIL"]
pw    = os.environ["__BZ_PW"]
base  = "http://{BESZEL["host"]}:{BESZEL["port"]}"
def req(url, data=None, method=None, token=None):
    headers = {{"Content-Type": "application/json"}}
    if token: headers["Authorization"] = token
    r = urllib.request.Request(base + url, data=data and json.dumps(data).encode(), headers=headers, method=method)
    return json.loads(urllib.request.urlopen(r).read())
try:
    su_token = req("/api/collections/_superusers/auth-with-password", {{"identity": email, "password": pw}})["token"]
    user_id  = req("/api/collections/users/records", token=su_token)["items"][0]["id"]
    req(f"/api/collections/users/records/{{user_id}}", {{"password": pw, "passwordConfirm": pw}}, "PATCH", su_token)
    print("beszel: user password synced")
except Exception as e:
    print(f"beszel: user password sync failed: {{e}}")
    raise SystemExit(1)
PYEOF
        unset __BZ_EMAIL __BZ_PW
        """,
    ],
)

# Fetch the hub universal token via API and write it to the agent env file.
# Only runs when TOKEN is empty (first deploy or after manual rotation).
# The "Sync beszel user password" step above already ensures the regular user's
# password matches BW, so we can auth directly here.
# Credentials passed via env so special chars in the password are safe.
server.shell(
    name="Fetch beszel universal token from hub API",
    commands=[
        f"""
        set -a; . /etc/secrets/beszel-hub.env; set +a
        # Skip if token already set
        CURRENT_TOKEN=$(grep -oP 'TOKEN=\\K.+' /etc/secrets/beszel-agent.env 2>/dev/null || true)
        if [ -n "$CURRENT_TOKEN" ]; then exit 0; fi
        if [ -z "$USER_EMAIL" ] || [ -z "$USER_PASSWORD" ]; then
          echo "beszel: USER_EMAIL/USER_PASSWORD not set, skipping token fetch" >&2; exit 0
        fi
        # Wait for hub to accept connections
        for i in $(seq 1 30); do
          curl -sf http://{BESZEL["host"]}:{BESZEL["port"]}/api/health >/dev/null 2>&1 && break
          sleep 1
        done
        # Auth as regular user (universal-token endpoint requires this, not superuser)
        export __BZ_EMAIL="$USER_EMAIL" __BZ_PW="$USER_PASSWORD"
        JWT=$(curl -sf -X POST http://{BESZEL["host"]}:{BESZEL["port"]}/api/collections/users/auth-with-password \
          -H 'Content-Type: application/json' \
          -d "$(python3 -c 'import json,os; print(json.dumps({{"identity":os.environ["__BZ_EMAIL"],"password":os.environ["__BZ_PW"]}}),end="")')" \
          | grep -oP '"token":"\\K[^"]+')
        unset __BZ_EMAIL __BZ_PW
        if [ -z "$JWT" ]; then
          echo "beszel: failed to authenticate for token fetch" >&2; exit 1
        fi
        # Enable and retrieve universal token
        TOKEN=$(curl -sf "http://{BESZEL["host"]}:{BESZEL["port"]}/api/beszel/universal-token?enable=1" \
          -H "Authorization: $JWT" | grep -oP '"token":"\\K[^"]+')
        if [ -z "$TOKEN" ]; then
          echo "beszel: failed to fetch universal token" >&2; exit 1
        fi
        HUB_KEY=$(ssh-keygen -y -f /var/lib/beszel-hub/beszel_data/id_ed25519)
        printf 'TOKEN=%s\nKEY=%s\n' "$TOKEN" "$HUB_KEY" > /etc/secrets/beszel-agent.env
        chmod 600 /etc/secrets/beszel-agent.env
        echo "beszel: universal token and hub key written to agent env"
        """,
    ],
)

# Sync hub public key into agent env on every deploy (stable but changes if
# hub data dir is wiped). Leaves TOKEN untouched.
server.shell(
    name="Sync beszel hub key into agent env",
    commands=[
        """
        KEY_FILE=/var/lib/beszel-hub/beszel_data/id_ed25519
        if [ ! -f "$KEY_FILE" ]; then exit 0; fi
        HUB_KEY=$(ssh-keygen -y -f "$KEY_FILE")
        CURRENT_KEY=$(grep -oP 'KEY=\\K.+' /etc/secrets/beszel-agent.env 2>/dev/null || true)
        if [ "$CURRENT_KEY" = "$HUB_KEY" ]; then exit 0; fi
        TOKEN=$(grep -oP 'TOKEN=\\K.+' /etc/secrets/beszel-agent.env 2>/dev/null || true)
        printf 'TOKEN=%s\nKEY=%s\n' "$TOKEN" "$HUB_KEY" > /etc/secrets/beszel-agent.env
        chmod 600 /etc/secrets/beszel-agent.env
        """,
    ],
)

server.shell(
    name="Restart beszel-agent if Quadlet or env changed",
    commands=[
        restart_if_changed(
            "beszel-agent", _agent_quadlet_hash, env_files=("/etc/secrets/beszel-agent.env",)
        )
    ],
)
