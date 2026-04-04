"""Trivy: CVE scan + native binary version checks via systemd timers."""

import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, NTFY, TRIVY

DOMAIN = NETWORK["domain"]
NTFY_URL = f"https://ntfy.{DOMAIN}/{NTFY['topic']}"

VERSION = TRIVY["version"]
BINARY_URL = (
    f"https://github.com/aquasecurity/trivy/releases/download/v{VERSION}/"
    f"trivy_{VERSION}_Linux-ARM64.tar.gz"
)

# --- Binary ---

server.shell(
    name=f"Install Trivy {VERSION}",
    commands=[
        f"""
        INSTALLED=$(/usr/local/bin/trivy --version 2>/dev/null | awk '/Version:/ {{print $2}}' || true)
        if [ "$INSTALLED" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" | tar -xz -C /usr/local/bin trivy
          chmod +x /usr/local/bin/trivy
        fi
        """,
    ],
)

# --- CVE scan script (runs weekly) ---
# Scans all running container images for HIGH/CRITICAL CVEs.
# Sends one ntfy notification per affected image.

cve_scan = f"""\
#!/bin/bash
set -euo pipefail

NTFY_URL="{NTFY_URL}"
export XDG_RUNTIME_DIR=/run

/usr/local/bin/trivy image --download-db-only --quiet 2>/dev/null

for image in $(podman ps --format "{{{{.Image}}}}" | sort -u); do
    if ! /usr/local/bin/trivy image \\
            --severity HIGH,CRITICAL \\
            --exit-code 1 \\
            --no-progress \\
            --quiet \\
            "$image" > /dev/null 2>&1; then
        curl -sf \\
            -H "Title: CVE Alert" \\
            -H "Priority: high" \\
            -H "Tags: warning" \\
            -d "HIGH/CRITICAL CVEs in ${{image}} — SSH to Pi: trivy image ${{image}}" \\
            "$NTFY_URL" > /dev/null || true
    fi
done
"""

# --- Binary version check script (runs daily) ---
# Checks Traefik and wg-portal against their latest GitHub releases.
# Sends one ntfy notification per outdated binary.

version_check = f"""\
#!/bin/bash
set -euo pipefail

NTFY_URL="{NTFY_URL}"

_latest() {{
    curl -sf "https://api.github.com/repos/$1/releases/latest" \\
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo ""
}}

_check() {{
    local name="$1" installed="$2" repo="$3"
    local latest
    latest=$(_latest "$repo")
    if [ -n "$installed" ] && [ -n "$latest" ] && [ "$installed" != "$latest" ]; then
        curl -sf \\
            -H "Title: Update Available" \\
            -H "Tags: arrow_up" \\
            -d "${{name}}: ${{installed}} → ${{latest}} — re-deploy to update" \\
            "$NTFY_URL" > /dev/null || true
    fi
}}

TRAEFIK_VER=$(/usr/local/bin/traefik version 2>/dev/null | awk '/Version:/ {{print "v"$2}}' || echo "")
_check "Traefik" "$TRAEFIK_VER" "traefik/traefik"

WGP_VER=$(/usr/local/bin/wg-portal --version 2>/dev/null | grep -oE 'v[0-9]+\\.[0-9]+\\.[0-9]+' | head -1 || echo "")
_check "wg-portal" "$WGP_VER" "h44z/wg-portal"
"""

# --- Systemd units ---

cve_service = """\
[Unit]
Description=Trivy CVE scan

[Service]
Type=oneshot
ExecStart=/usr/local/bin/trivy-cve-scan.sh
"""

cve_timer = """\
[Unit]
Description=Weekly Trivy CVE scan

[Timer]
OnCalendar=weekly
Persistent=true
RandomizedDelaySec=3600

[Install]
WantedBy=timers.target
"""

versions_service = """\
[Unit]
Description=Check native binary versions against GitHub releases

[Service]
Type=oneshot
ExecStart=/usr/local/bin/check-versions.sh
"""

versions_timer = """\
[Unit]
Description=Daily binary version check

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=1800

[Install]
WantedBy=timers.target
"""

for dest, content, mode in [
    ("/usr/local/bin/trivy-cve-scan.sh", cve_scan, "755"),
    ("/usr/local/bin/check-versions.sh", version_check, "755"),
    ("/etc/systemd/system/trivy-cve-scan.service", cve_service, "644"),
    ("/etc/systemd/system/trivy-cve-scan.timer", cve_timer, "644"),
    ("/etc/systemd/system/check-versions.service", versions_service, "644"),
    ("/etc/systemd/system/check-versions.timer", versions_timer, "644"),
]:
    files.put(
        name=f"Write {dest.split('/')[-1]}",
        src=io.BytesIO(content.encode()),
        dest=dest,
        user="root",
        group="root",
        mode=mode,
    )

for timer in ("trivy-cve-scan.timer", "check-versions.timer"):
    systemd.service(
        name=f"Enable {timer}",
        service=timer,
        enabled=True,
        running=True,
        daemon_reload=True,
    )
