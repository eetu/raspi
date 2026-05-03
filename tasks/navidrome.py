"""Navidrome: Podman Quadlet container unit for music streaming."""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import KANIDM_OIDC_CLIENTS, NAVIDROME
from tasks.util import resolve_latest

_image = (
    resolve_latest("deluan/navidrome", NAVIDROME["image"])
    if NAVIDROME.get("resolve_latest")
    else NAVIDROME["image"]
)

# Two auth modes:
#  - oauth2-proxy active → the music router uses oauth2-chain-music; clients
#    authenticate via Kanidm SSO (Flo's "Login with IAP" or any browser-based
#    Subsonic client) and Navidrome auto-creates users from the trusted
#    X-Auth-Request-User header on loopback. No admin-user bootstrap needed.
#  - oauth2-proxy not active → the music router exposes Navidrome directly;
#    we bootstrap an admin user from the `navidrome` Bitwarden item so plain
#    Subsonic clients can log in with username/password.
_oauth2_client = KANIDM_OIDC_CLIENTS.get("oauth2-proxy")
_oauth2_active = bool(_oauth2_client and bw.kanidm_oidc_secret(_oauth2_client["secret_field"]))

quadlet = f"""\
[Unit]
Description=Navidrome
After=network-online.target mnt-music.automount
Wants=network-online.target mnt-music.automount

[Container]
ContainerName=navidrome
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
Environment=ND_ENABLEEXTERNALSERVICES=false
Environment=ND_LASTFM_ENABLED=false
Environment=ND_LISTENBRAINZ_ENABLED=false
Environment=ND_UPDATECHECK=false
Environment=ND_COVERARTPRIORITY=embedded,cover.*,folder.*,front.*
# Trust X-Auth-Request-User from Traefik on loopback. Only consulted when
# oauth2-proxy is in front of the music router — otherwise the header is
# never set and Navidrome falls back to its own username/password auth.
Environment=ND_REVERSEPROXYUSERHEADER=X-Auth-Request-User
Environment=ND_REVERSEPROXYWHITELIST=127.0.0.1/32
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=256M

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

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

if not _oauth2_active:
    # No SSO — seed the admin user from Bitwarden so Subsonic clients can
    # log in. /auth/createAdmin is a no-op once a user exists, so re-running
    # the deploy is safe; rotating the BW password however does NOT propagate
    # to an existing admin user — that requires a manual reset.
    _creds = bw.navidrome_creds()
    _nd_password_json = json.dumps(_creds["password"])
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
