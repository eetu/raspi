"""Audiobookshelf: Podman Quadlet container unit (arm64-safe)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import AUDIOBOOKSHELF, CIFS
from tasks.util import resolve_latest

_image = (
    resolve_latest("advplyr/audiobookshelf", AUDIOBOOKSHELF["image"])
    if AUDIOBOOKSHELF.get("resolve_latest")
    else AUDIOBOOKSHELF["image"]
)

quadlet = f"""\
[Unit]
Description=Audiobookshelf
After=network-online.target mnt-audiobooks.automount
Wants=network-online.target mnt-audiobooks.automount

[Container]
Image={_image}
Network=host
Volume={CIFS["mountpoint"]}/OpenAudible/books:/audiobooks:ro
Volume=/var/lib/audiobookshelf/config:/config
Volume=/var/lib/audiobookshelf/metadata:/metadata
Environment=TZ=Europe/Helsinki
Environment=PORT={AUDIOBOOKSHELF["port"]}
Environment=HOST={AUDIOBOOKSHELF["host"]}
AutoUpdate=registry
Pull=newer
HealthCmd=CMD-SHELL nc -z 127.0.0.1 {AUDIOBOOKSHELF["port"]}
HealthInterval=30s
HealthTimeout=5s
HealthRetries=3
HealthStartPeriod=60s

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=256M

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()
_creds = bw.abs_creds()

files.directory(
    name="Create audiobookshelf config dir",
    path="/var/lib/audiobookshelf/config",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.directory(
    name="Create audiobookshelf metadata dir",
    path="/var/lib/audiobookshelf/metadata",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write audiobookshelf.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/audiobookshelf.container",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Reload quadlet units",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true",
    ],
)

systemd.service(
    name="Start Audiobookshelf",
    service="audiobookshelf",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Initialize Audiobookshelf root user",
    commands=[
        f"""
        ABS_URL="http://{AUDIOBOOKSHELF["host"]}:{AUDIOBOOKSHELF["port"]}"
        for i in $(seq 1 10); do
          STATUS=$(curl -s -o /dev/null -w '%{{http_code}}' "$ABS_URL/ping" 2>/dev/null || true)
          if [ "$STATUS" = "200" ]; then break; fi
          sleep 2
        done
        curl -sf -X POST "$ABS_URL/init" \
          -H "Content-Type: application/json" \
          -d '{{"newRoot":{{"username":"{_creds["username"]}","password":"{_creds["password"]}"}}}}'  \
          2>/dev/null || true
        """,
    ],
)

server.shell(
    name="Restart Audiobookshelf if quadlet changed",
    commands=[
        f"""
        STAMP=/etc/containers/systemd/.audiobookshelf-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart audiobookshelf
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)
