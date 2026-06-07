"""Beszel agent (native binary) for off-hub hosts — reports this host to the
raspi beszel hub over the LAN.

`telemetry` feature. Unlike tasks/beszel.py (which runs the hub plus a Podman
agent on raspi), this is a single sandboxed native binary: no podman, no
docker.sock — a camera node has only itself to report. It connects *outbound*
to the hub using the universal token (websocket mode), so it needs no inbound
port and no SSH listener exposed to the hub.

Token + hub key handling:
  * KEY — the hub's ed25519 *public* key — is read control-side from raspi at
    plan time (a public key, safe to transport) and baked into the agent env.
  * TOKEN — the universal registration token — is reconciled on-device from the
    hub API using the token_fetch user (BESZEL["users"]), mirroring
    tasks/beszel.py: any existing token is promoted to permanent in place so a
    re-deploy keeps the running registration valid rather than minting a fresh
    UUID. Requires BW_SESSION (token_fetch user password) at deploy time.

Egress: "beszel-agent" is in tasks/network_restrict.py RESTRICTED, so the agent
can reach the hub on the LAN but nothing on the internet.
"""

import hashlib
import io
import subprocess

from pyinfra.operations import files, server, systemd

import vault
from group_data.all import NETWORK
from tasks.util import optional, restart_if_changed

BESZEL = optional("BESZEL")


def _hub_pubkey() -> str:
    """Read raspi's beszel hub ed25519 *public* key, control-side.

    ssh-keygen -y derives the public key from the hub's private key file; only
    the public half is emitted, so this transports nothing secret. Mirrors the
    cross-host control probe pattern in tasks/restic.py."""
    try:
        r = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=5",
                "raspi",
                "sudo ssh-keygen -y -f /var/lib/beszel-hub/beszel_data/id_ed25519",
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        return r.stdout.strip()
    except Exception:
        return ""


if BESZEL is None:
    # Beszel retired: stop + disable the agent, leave /var/lib/beszel-agent and
    # the env files on disk so re-adding the dict restores it cleanly.
    systemd.service(
        name="Stop + disable beszel-agent (kept on disk for rollback)",
        service="beszel-agent",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    VERSION = BESZEL["version"]
    HUB_URL = f"http://{NETWORK['lan_ip']}:{BESZEL['port']}"
    AGENT_URL = (
        f"https://github.com/henrygd/beszel/releases/download/{VERSION}/"
        f"beszel-agent_linux_arm64.tar.gz"
    )

    _token_fetch = next((u for u in BESZEL.get("users", []) if u.get("token_fetch")), None)
    _hub_key = _hub_pubkey()
    if not _hub_key:
        print("WARN: could not read raspi hub public key — beszel-agent KEY will be empty")

    # --- Agent binary ---

    server.shell(
        name=f"Install beszel-agent {VERSION}",
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

    files.directory(
        name="Create beszel-agent data dir",
        path="/var/lib/beszel-agent",
        user="root",
        group="root",
        mode="700",
        present=True,
    )

    # --- Bootstrap creds (token_fetch user) ---
    # Written from BW as file content (never shell args) so the password doesn't
    # land in the deploy transcript. Sourced by the reconcile step below.

    if _token_fetch:
        _tf_pw = vault.beszel_user_password(_token_fetch["email"])
        files.put(
            name="Write beszel-agent bootstrap creds",
            src=io.BytesIO(
                f"FETCH_EMAIL={_token_fetch['email']}\nFETCH_PASSWORD={_tf_pw}\n".encode()
            ),
            dest="/etc/secrets/beszel-agent-bootstrap.env",
            user="root",
            group="root",
            mode="600",
        )

    # --- Reconcile TOKEN (on-device) + bake KEY/HUB_URL into the agent env ---

    server.shell(
        name="Reconcile beszel-agent token + hub key from hub API",
        commands=[
            f"""
            for i in $(seq 1 30); do
              curl -sf {HUB_URL}/api/health >/dev/null 2>&1 && break
              sleep 2
            done
            set -a
            . /etc/secrets/beszel-agent-bootstrap.env 2>/dev/null || true
            set +a
            python3 << 'PYEOF'
import json, os, urllib.error, urllib.request
base = "{HUB_URL}"

def req(url, data=None, method=None, token=None):
    headers = {{"Content-Type": "application/json"}}
    if token: headers["Authorization"] = token
    r = urllib.request.Request(base + url, data=data and json.dumps(data).encode(), headers=headers, method=method)
    return json.loads(urllib.request.urlopen(r).read())

email = os.environ.get("FETCH_EMAIL", "")
pw = os.environ.get("FETCH_PASSWORD", "")
if not email or not pw:
    print("beszel-agent: no token_fetch creds — skipping token reconcile"); raise SystemExit(0)

try:
    jwt = req("/api/collections/users/auth-with-password", {{"identity": email, "password": pw}})["token"]
except urllib.error.HTTPError as e:
    print(f"beszel-agent: hub auth failed ({{e.code}})"); raise SystemExit(1)

# Promote any existing token to permanent in place (don't mint a fresh UUID).
current = ""
try:
    for line in open("/etc/secrets/beszel-agent.env"):
        if line.startswith("TOKEN="):
            current = line.split("=", 1)[1].strip()
except FileNotFoundError:
    pass

url = "/api/beszel/universal-token?enable=1&permanent=1"
if current:
    url += f"&token={{current}}"
token = req(url, token=jwt).get("token", "")
if not token:
    print("beszel-agent: failed to fetch universal token"); raise SystemExit(1)

key = "{_hub_key}"
with open("/etc/secrets/beszel-agent.env", "w") as f:
    f.write(f"TOKEN={{token}}\\nKEY={{key}}\\nHUB_URL={{base}}\\n")
os.chmod("/etc/secrets/beszel-agent.env", 0o600)
print("beszel-agent: token + hub key + hub url written to agent env")
PYEOF
            """,
        ],
    )

    # --- systemd unit (native, sandboxed) ---

    agent_unit = """\
[Unit]
Description=Beszel monitoring agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/secrets/beszel-agent.env
Environment=LISTEN=45876
Environment=DATA_DIR=/var/lib/beszel-agent
ExecStart=/usr/local/bin/beszel-agent
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=64M
ProtectSystem=strict
ReadWritePaths=/var/lib/beszel-agent
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
ProtectClock=yes
ProtectHostname=yes
RestrictNamespaces=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RemoveIPC=yes
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
"""

    files.put(
        name="Write beszel-agent systemd unit",
        src=io.BytesIO(agent_unit.encode()),
        dest="/etc/systemd/system/beszel-agent.service",
        user="root",
        group="root",
        mode="644",
    )

    _unit_hash = hashlib.sha256(agent_unit.encode()).hexdigest()

    systemd.service(
        name="Enable beszel-agent",
        service="beszel-agent",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart beszel-agent if unit or env changed",
        commands=[
            restart_if_changed(
                "beszel-agent", _unit_hash, env_files=("/etc/secrets/beszel-agent.env",)
            )
        ],
    )
