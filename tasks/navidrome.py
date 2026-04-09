"""Navidrome: Podman Quadlet container unit for music streaming."""

import hashlib
import io
import json
import random
import string

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import NAVIDROME
from tasks.util import resolve_latest

_image = (
    resolve_latest("deluan/navidrome", NAVIDROME["image"])
    if NAVIDROME.get("resolve_latest")
    else NAVIDROME["image"]
)

quadlet = f"""\
[Unit]
Description=Navidrome
After=network-online.target mnt-music.automount
Wants=network-online.target mnt-music.automount

[Container]
Image={_image}
Network=host
Volume=/var/lib/navidrome:/data
Volume=/mnt/music:/music:ro
Environment=TZ=Europe/Helsinki
Environment=ND_MUSICFOLDER=/music
Environment=ND_DATAFOLDER=/data
Environment=ND_PORT={NAVIDROME["port"]}
Environment=ND_ADDRESS={NAVIDROME["host"]}
Environment=ND_LOGLEVEL=warn
Environment=ND_SCANNER_SCHEDULE=@hourly
Environment=ND_SESSIONTIMEOUT=168h
Environment=ND_ENABLEINSIGHTSCOLLECTOR=false
AutoUpdate=registry
Pull=newer
HealthCmd=CMD-SHELL nc -z 127.0.0.1 {NAVIDROME["port"]}
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
_creds = bw.navidrome_creds()
_nd_password_json = json.dumps(_creds["password"])

# Subsonic token auth: t=md5(password+salt), generated fresh each deploy
_salt = "".join(random.choices(string.ascii_lowercase, k=8))
_token = hashlib.md5((_creds["password"] + _salt).encode()).hexdigest()
_subsonic_auth = f"u={_creds['username']}&t={_token}&s={_salt}&v=1.16.1&c=raspi&f=json"

files.directory(
    name="Create navidrome data dir",
    path="/var/lib/navidrome",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write navidrome.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/navidrome.container",
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
    name="Start Navidrome",
    service="navidrome",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Initialize Navidrome admin user",
    commands=[
        f"""
        ND_URL="http://{NAVIDROME["host"]}:{NAVIDROME["port"]}"
        for i in $(seq 1 15); do
          STATUS=$(curl -s -o /dev/null -w '%{{http_code}}' "$ND_URL/ping" 2>/dev/null || true)
          if [ "$STATUS" = "200" ]; then break; fi
          sleep 3
        done
        # /auth/createAdmin only succeeds when no users exist — safe to call on every deploy
        curl -sf -X POST "$ND_URL/auth/createAdmin" \
          -H "Content-Type: application/json" \
          -d '{{"username":"{_creds["username"]}","password":{_nd_password_json}}}' \
          2>/dev/null || true
        """,
    ],
)

server.shell(
    name="Restart Navidrome if quadlet changed",
    commands=[
        f"""
        STAMP=/etc/containers/systemd/.navidrome-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart navidrome
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)

server.shell(
    name="Trigger Navidrome library scan",
    commands=[
        f"""
        ND_URL="http://{NAVIDROME["host"]}:{NAVIDROME["port"]}"
        curl -sf "$ND_URL/rest/startScan.view?{_subsonic_auth}" > /dev/null 2>&1 || true
        """,
    ],
)
