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
CACHE_DIR=/var/lib/trivy/cache
# /tmp is a 32M tmpfs — point trivy at the SD-backed dir for DB downloads.
export TMPDIR=/var/lib/trivy/tmp
export XDG_RUNTIME_DIR=/run
export DOCKER_HOST=unix:///run/podman/podman.sock

# Drop --quiet so DB freshness/version lands in the journal — useful to
# diagnose silent-alert situations after a long gap between scans.
/usr/local/bin/trivy image \\
    --cache-dir "$CACHE_DIR" \\
    --download-db-only

for image in $(podman ps --format "{{{{.Image}}}}" | sort -u); do
    json=$(/usr/local/bin/trivy image \\
            --cache-dir "$CACHE_DIR" \\
            --severity HIGH,CRITICAL \\
            --no-progress \\
            --quiet \\
            --format json \\
            "$image" 2>/dev/null) || true

    output=$(echo "$json" | python3 -c '
import json, sys
data = json.load(sys.stdin)
crits, highs = [], []
for r in data.get("Results", []):
    for v in (r.get("Vulnerabilities") or []):
        name = "{{}} {{}}".format(v["PkgName"], v.get("InstalledVersion", ""))
        title = v.get("Title", "")[:55]
        cve = v["VulnerabilityID"]
        line = "• {{}} — {{}} ({{}})".format(name, title, cve)
        if v["Severity"] == "CRITICAL":
            crits.append(line)
        else:
            highs.append(line)
if not crits and not highs:
    sys.exit(0)
priority = "urgent" if crits else "high"
parts = ["{{}} CRITICAL • {{}} HIGH".format(len(crits), len(highs))]
if crits:
    parts.append("CRITICAL:\\n" + "\\n".join(crits))
if highs:
    shown = highs[:3]
    label = "HIGH ({{}} of {{}}):\\n".format(len(shown), len(highs)) if len(highs) > 3 else "HIGH:\\n"
    parts.append(label + "\\n".join(shown))
print(priority)
print("\\n\\n".join(parts))
' 2>/dev/null) || true

    [ -z "$output" ] && continue

    priority=$(printf '%s' "$output" | head -1)
    msg=$(printf '%s' "$output" | tail -n +2)
    short="${{image##*/}}"
    tags=warning
    [ "$priority" = urgent ] && tags=rotating_light

    curl -sf \\
        -H "Title: CVE: ${{short}}" \\
        -H "Priority: $priority" \\
        -H "Tags: $tags" \\
        -d "$msg" \\
        "$NTFY_URL" > /dev/null || true
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
Environment=TMPDIR=/var/lib/trivy/tmp
ExecStart=/usr/local/bin/trivy-cve-scan.sh
"""

cve_timer = """\
[Unit]
Description=Twice-weekly Trivy CVE scan

[Timer]
OnCalendar=Mon,Thu *-*-* 02:00:00
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

for _dir in ("/var/lib/trivy/tmp", "/var/lib/trivy/cache"):
    files.directory(
        name=f"Create {_dir}",
        path=_dir,
        user="root",
        group="root",
        mode="700",
        present=True,
    )

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
