"""Audiobookshelf: Podman Quadlet container unit (arm64-safe)."""

import hashlib
import io
import json
import re
import urllib.request

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import AUDIOBOOKSHELF, CIFS


def _latest_abs_tag() -> str:
    """Query GitHub releases for the latest tag matching the current major version."""
    major = AUDIOBOOKSHELF["image"].split(":")[-1].split(".")[0]
    pattern = re.compile(rf"^{re.escape(major)}\.\d+\.\d+$")

    req = urllib.request.Request(
        "https://api.github.com/repos/advplyr/audiobookshelf/releases?per_page=5",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req) as r:
        releases = json.loads(r.read())

    matching = [
        r["tag_name"].lstrip("v") for r in releases if pattern.match(r["tag_name"].lstrip("v"))
    ]
    if not matching:
        raise RuntimeError(f"No audiobookshelf releases found for major version {major}")
    return matching[0]


_image = (
    f"ghcr.io/advplyr/audiobookshelf:{_latest_abs_tag()}"
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
