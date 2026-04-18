"""VuIO: DLNA media server for LAN streaming (native Rust binary)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import VUIO
from tasks.util import restart_if_changed

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

# --- Config ---

config_toml = f"""\
[server]
port = {VUIO["port"]}
interface = "0.0.0.0"
name = "Raspi"
uuid = "c12603c5-840b-4987-849d-8a5e75d60192"
ip = ""

[network]
interface_selection = "Auto"
multicast_ttl = 4
announce_interval_seconds = 30

[media]
scan_on_startup = true
watch_for_changes = true
cleanup_deleted_files = true
autoplay_enabled = true
supported_extensions = ["mp4", "mkv", "avi", "mov", "wmv", "flv", "webm", "m4v", "mpg", "mpeg", "3gp", "ogv", "mp3", "flac", "wav", "aac", "ogg", "wma", "m4a", "opus", "ape", "jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "svg", "mka", "mks"]

[[media.directories]]
path = "{VUIO["movies_path"]}"
recursive = true
exclude_patterns = [".*", "*.tmp", "*.temp", "lost+found", ".Trash-*"]
validation_mode = "Warn"

[database]
path = "/var/lib/vuio/media.db"
vacuum_on_startup = false
backup_enabled = false
"""

files.put(
    name="Write vuio config",
    src=io.BytesIO(config_toml.encode()),
    dest="/var/lib/vuio/config.toml",
    user="root",
    group="root",
    mode="644",
)

# --- systemd service ---

service_unit = f"""\
[Unit]
Description=VuIO DLNA Media Server
After=network-online.target mnt-movies.automount
Wants=network-online.target mnt-movies.automount

[Service]
Type=simple
ExecStart=/usr/local/bin/vuio -c /var/lib/vuio/config.toml
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

_unit_hash = hashlib.sha256((service_unit + config_toml).encode()).hexdigest()

systemd.service(
    name="Enable vuio",
    service="vuio",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart vuio if unit changed",
    commands=[restart_if_changed("vuio", _unit_hash)],
)
