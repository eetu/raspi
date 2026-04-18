"""Syncthing: continuous file synchronization (native binary)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import SYNCTHING
from tasks.util import restart_if_changed

VERSION = SYNCTHING["version"]
USER = SYNCTHING.get("user", "root")
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
    user=USER,
    group=USER,
    mode="700",
    present=True,
)

# --- Initial config with GUI credentials (first run only) ---

server.shell(
    name="Initialize Syncthing config",
    commands=[
        f"""
        if [ ! -f /var/lib/syncthing/config.xml ]; then
          runuser -u {USER} -- syncthing generate \
            --config=/var/lib/syncthing \
            --data=/var/lib/syncthing \
            --gui-user={_creds["username"]!r} \
            --gui-password={_creds["password"]!r}
        fi
        """,
    ],
)

# --- Reverse proxy + LAN-only config via direct XML patch ---
# Patches config.xml before the daemon starts so settings are guaranteed on first run.
# Restarts the daemon if it is already running so the new config takes effect.

server.shell(
    name="Configure Syncthing: reverse proxy + LAN-only",
    commands=[
        f"""
        python3 -c '
import xml.etree.ElementTree as ET
p = "/var/lib/syncthing/config.xml"
tree = ET.parse(p)
root = tree.getroot()
opts = root.find("options")
for key, val in [("globalAnnounceEnabled","false"),("relaysEnabled","false"),
                 ("natEnabled","false"),("urAccepted","-1")]:
    el = opts.find(key)
    if el is None: el = ET.SubElement(opts, key)
    el.text = val
gui = root.find("gui")
el = gui.find("insecureSkipHostcheck")
if el is None: el = ET.SubElement(gui, "insecureSkipHostcheck")
el.text = "true"
tree.write(p, encoding="utf-8", xml_declaration=True)
'
        chown {USER}:{USER} /var/lib/syncthing/config.xml
        systemctl is-active --quiet syncthing && systemctl reload-or-restart syncthing || true
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
User={USER}
Environment=HOME=/{"root" if USER == "root" else f"home/{USER}"}
ExecStart=/usr/local/bin/syncthing serve \
  --no-browser --no-restart \
  --config=/var/lib/syncthing \
  --data=/var/lib/syncthing \
  --gui-address=http://{SYNCTHING["host"]}:{SYNCTHING["port"]}
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=256M
ProtectSystem=strict
ReadWritePaths=/var/lib/syncthing /home/{USER}
ProtectHome=no
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
    commands=[restart_if_changed("syncthing", _unit_hash)],
)
