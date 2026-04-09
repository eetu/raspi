"""Audiobookshelf: Podman Quadlet container unit (arm64-safe)."""

import hashlib
import io
import json
import subprocess

from pyinfra import logger
from pyinfra.operations import files, python, server, systemd

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
Volume={CIFS["audiobooks"]["mountpoint"]}/OpenAudible/books:/audiobooks:ro
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
# Pre-encode password as a JSON string so it's safely embeddable in shell regardless of special chars
_abs_password_json = json.dumps(_creds["password"])

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
    name="Configure Audiobookshelf library and save API key",
    commands=[
        f"""
        ABS_URL="http://{AUDIOBOOKSHELF["host"]}:{AUDIOBOOKSHELF["port"]}"

        # Wait for ABS to be ready
        for i in $(seq 1 15); do
          STATUS=$(curl -s -o /dev/null -w '%{{http_code}}' "$ABS_URL/ping" 2>/dev/null || true)
          if [ "$STATUS" = "200" ]; then break; fi
          sleep 2
        done

        # Read token directly from the DB — avoids depending on password matching
        DB=/var/lib/audiobookshelf/config/absdatabase.sqlite
        TOKEN=$(sqlite3 "$DB" "SELECT token FROM users WHERE type='root' LIMIT 1;" 2>/dev/null || true)

        if [ -z "$TOKEN" ]; then
          echo "ABS: could not obtain token, skipping library setup" >&2
          exit 0
        fi

        # Sync password from Bitwarden into ABS (allows rotation via BW + redeploy)
        USER_ID=$(sqlite3 "$DB" "SELECT id FROM users WHERE type='root' LIMIT 1;" 2>/dev/null || true)
        curl -sf -X PATCH "http://127.0.0.1:13378/api/users/$USER_ID" \
          -H "Authorization: Bearer $TOKEN" \
          -H "Content-Type: application/json" \
          -d '{{"password":{_abs_password_json}}}' \
          > /dev/null 2>&1 || true

        # Scoped API key: create only if secrets file is absent.
        # To rotate: delete /etc/secrets/audiobookshelf-api-key on the Pi and redeploy.
        if [ ! -f /etc/secrets/audiobookshelf-api-key ]; then
          # Delete existing 'mobile' key if present (rotation case)
          EXISTING_ID=$(curl -sf "$ABS_URL/api/api-keys" \
            -H "Authorization: Bearer $TOKEN" \
            | python3 -c "
import sys, json
keys = json.load(sys.stdin).get('apiKeys', [])
m = next((k for k in keys if k['name'] == 'mobile'), None)
print(m['id'] if m else '')
" 2>/dev/null || true)
          if [ -n "$EXISTING_ID" ]; then
            curl -sf -X DELETE "$ABS_URL/api/api-keys/$EXISTING_ID" \
              -H "Authorization: Bearer $TOKEN" > /dev/null 2>&1 || true
          fi

          # Create and activate new scoped key (acts on behalf of root user)
          RESULT=$(curl -sf -X POST "$ABS_URL/api/api-keys" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "{{\\"name\\":\\"mobile\\",\\"userId\\":\\"$USER_ID\\"}}" 2>/dev/null || true)
          KEY_ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['apiKey']['id'])" 2>/dev/null || true)
          API_KEY=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['apiKey']['apiKey'])" 2>/dev/null || true)

          if [ -z "$API_KEY" ]; then
            echo "ABS: failed to create API key" >&2
          else
            curl -sf -X PATCH "$ABS_URL/api/api-keys/$KEY_ID" \
              -H "Authorization: Bearer $TOKEN" \
              -H "Content-Type: application/json" \
              -d '{{"isActive":true}}' > /dev/null 2>&1 || true
            echo "$API_KEY" > /etc/secrets/audiobookshelf-api-key
            chmod 600 /etc/secrets/audiobookshelf-api-key
          fi
        fi

        # Create library if it doesn't exist yet
        LIB_EXISTS=$(curl -sf "$ABS_URL/api/libraries" \
          -H "Authorization: Bearer $TOKEN" \
          | python3 -c "
import sys, json
libs = json.load(sys.stdin).get('libraries', [])
print('yes' if any(f['fullPath'] == '/audiobooks' for l in libs for f in l.get('folders', [])) else 'no')
" 2>/dev/null || echo "no")

        if [ "$LIB_EXISTS" = "no" ]; then
          curl -sf -X POST "$ABS_URL/api/libraries" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d '{{"name":"Audiobooks","folders":[{{"fullPath":"/audiobooks"}}],"icon":"audiobookshelf","mediaType":"book","settings":{{"coverAspectRatio":1,"disableWatcher":false,"autoScanCronExpression":"0 * * * *"}}}}' \
            2>/dev/null || true
        fi
        """,
    ],
)


def _save_api_key_to_bw():
    """Sync ABS API key from Pi secrets file to Bitwarden."""
    result = subprocess.run(
        ["ssh", "raspi", "sudo", "cat", "/etc/secrets/audiobookshelf-api-key"],
        capture_output=True,
        text=True,
    )
    token = result.stdout.strip()
    if not token:
        logger.warning("ABS: no API key found on Pi, skipping Bitwarden update")
        return
    if bw.abs_api_key() == token:
        return
    bw.save_abs_api_key(token)
    logger.info("ABS: synced API key to Bitwarden")


python.call(
    name="Save ABS API key to Bitwarden",
    function=_save_api_key_to_bw,
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
