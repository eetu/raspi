"""Network breach monitor: alerts when LAN-only services attempt blocked connections."""

import io

from pyinfra.operations import files, systemd

from group_data.all import NETWORK, NTFY

NTFY_URL = f"https://ntfy.{NETWORK['domain']}/{NTFY['topic']}"

check_script = f"""\
#!/bin/bash
set -euo pipefail

NTFY_URL="{NTFY_URL}"
STAMP="/run/network-breach.stamp"
LAST_CHECK=$(cat "$STAMP" 2>/dev/null || echo "1 hour ago")

# Search kernel log for nftables BREACH entries since last check
HITS=$(journalctl -k --since "$LAST_CHECK" --no-pager -q --grep "BREACH:" 2>/dev/null || true)

if [ -n "$HITS" ]; then
    # Group by service name and send one alert per service
    echo "$HITS" | grep -oP 'BREACH:\\K[^:]+' | sort -u | while read -r svc; do
        COUNT=$(echo "$HITS" | grep -c "BREACH:$svc:" || true)
        SAMPLE=$(echo "$HITS" | grep "BREACH:$svc:" | tail -1 | grep -oP 'DST=\\K[^ ]+' || echo "unknown")
        curl -sf \\
            -H "Title: Network Breach Attempt" \\
            -H "Priority: urgent" \\
            -H "Tags: skull" \\
            -d "${{svc}}: ${{COUNT}} blocked outbound packets (last dest: ${{SAMPLE}})" \\
            "$NTFY_URL" > /dev/null || true
    done
fi

date -Iseconds > "$STAMP"
"""

service_unit = """\
[Unit]
Description=Check for blocked network access from restricted services

[Service]
Type=oneshot
ExecStart=/usr/local/bin/check-network-breaches.sh
"""

timer_unit = """\
[Unit]
Description=Periodic network breach check

[Timer]
OnCalendar=*:0/15
Persistent=true

[Install]
WantedBy=timers.target
"""

for dest, content, mode in [
    ("/usr/local/bin/check-network-breaches.sh", check_script, "755"),
    ("/etc/systemd/system/check-network-breaches.service", service_unit, "644"),
    ("/etc/systemd/system/check-network-breaches.timer", timer_unit, "644"),
]:
    files.put(
        name=f"Write {dest.split('/')[-1]}",
        src=io.BytesIO(content.encode()),
        dest=dest,
        user="root",
        group="root",
        mode=mode,
    )

systemd.service(
    name="Enable check-network-breaches.timer",
    service="check-network-breaches.timer",
    enabled=True,
    running=True,
    daemon_reload=True,
)
