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
    """Query ghcr.io for the latest tag matching the current major version."""
    major = AUDIOBOOKSHELF["image"].split(":")[-1].split(".")[0]
    pattern = re.compile(rf"^{re.escape(major)}\.\d+\.\d+$")

    with urllib.request.urlopen(
        "https://ghcr.io/token?scope=repository:advplyr/audiobookshelf:pull&service=ghcr.io"
    ) as r:
        token = json.loads(r.read())["token"]

    tags: list[str] = []
    url: str | None = "https://ghcr.io/v2/advplyr/audiobookshelf/tags/list?n=1000"
    while url:
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(req) as r:
            tags.extend(json.loads(r.read()).get("tags", []))
            link = r.headers.get("Link", "")
        url = next(
            (p.split(";")[0].strip().strip("<>") for p in link.split(",") if 'rel="next"' in p),
            None,
        )

    matching = sorted(
        (t for t in tags if pattern.match(t)),
        key=lambda t: tuple(int(x) for x in t.split(".")),
    )
    if not matching:
        raise RuntimeError(f"No audiobookshelf tags found for major version {major}")
    return matching[-1]


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
