"""Cloudflare DDNS: shell script + systemd timer to keep wg.anarkisti.com current."""

import io

from pyinfra.operations import files, systemd

from group_data.all import NETWORK

DOMAIN = NETWORK["domain"]

script = f"""\
#!/bin/bash
set -euo pipefail

CF_TOKEN=$(grep '^CF_DNS_API_TOKEN=' /etc/secrets/cloudflare.env | cut -d= -f2-)
ZONE_ID=$(grep '^zone_id=' /etc/secrets/cloudflare.env | cut -d= -f2-)
RECORD_NAME="wg.{DOMAIN}"

_update_record() {{
    local TYPE="$1" CURRENT="$2"
    [ -z "$CURRENT" ] && return 0

    RECORD=$(curl -sf "https://api.cloudflare.com/client/v4/zones/${{ZONE_ID}}/dns_records?name=${{RECORD_NAME}}&type=${{TYPE}}" \\
        -H "Authorization: Bearer ${{CF_TOKEN}}")
    RECORD_ID=$(echo "$RECORD" | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['id'])" 2>/dev/null || true)
    DNS_IP=$(echo "$RECORD"   | python3 -c "import sys,json; print(json.load(sys.stdin)['result'][0]['content'])" 2>/dev/null || true)

    if [ "$CURRENT" = "$DNS_IP" ]; then return 0; fi

    if [ -n "$RECORD_ID" ]; then
        curl -sf -X PUT "https://api.cloudflare.com/client/v4/zones/${{ZONE_ID}}/dns_records/${{RECORD_ID}}" \\
            -H "Authorization: Bearer ${{CF_TOKEN}}" \\
            -H "Content-Type: application/json" \\
            --data "{{\\"type\\":\\"${{TYPE}}\\",\\"name\\":\\"${{RECORD_NAME}}\\",\\"content\\":\\"${{CURRENT}}\\",\\"ttl\\":120}}" > /dev/null
    else
        curl -sf -X POST "https://api.cloudflare.com/client/v4/zones/${{ZONE_ID}}/dns_records" \\
            -H "Authorization: Bearer ${{CF_TOKEN}}" \\
            -H "Content-Type: application/json" \\
            --data "{{\\"type\\":\\"${{TYPE}}\\",\\"name\\":\\"${{RECORD_NAME}}\\",\\"content\\":\\"${{CURRENT}}\\",\\"ttl\\":120}}" > /dev/null
    fi
    logger "cloudflare-ddns: updated ${{RECORD_NAME}} ${{TYPE}} ${{DNS_IP}} -> ${{CURRENT}}"
}}

CURRENT_IP=$(curl -sf https://api4.ipify.org || curl -sf https://ipv4.icanhazip.com || curl -sf https://ipv4.wtfismyip.com/text || true)
CURRENT_IP6=$(ip -6 addr show eth0 | awk '/inet6 2/ {{print $2}}' | cut -d/ -f1 | head -1)

_update_record A "$CURRENT_IP"
_update_record AAAA "$CURRENT_IP6"
"""

ddns_service = """\
[Unit]
Description=Cloudflare DDNS update
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/bin/cloudflare-ddns.sh
"""

ddns_timer = """\
[Unit]
Description=Cloudflare DDNS update timer

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
"""

files.put(
    name="Write cloudflare-ddns.sh",
    src=io.BytesIO(script.encode()),
    dest="/usr/local/bin/cloudflare-ddns.sh",
    user="root",
    group="root",
    mode="755",
)

files.put(
    name="Write cloudflare-ddns.service",
    src=io.BytesIO(ddns_service.encode()),
    dest="/etc/systemd/system/cloudflare-ddns.service",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write cloudflare-ddns.timer",
    src=io.BytesIO(ddns_timer.encode()),
    dest="/etc/systemd/system/cloudflare-ddns.timer",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable cloudflare-ddns.timer",
    service="cloudflare-ddns.timer",
    enabled=True,
    running=True,
    daemon_reload=True,
)
