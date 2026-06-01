"""Trivy: CVE scan + native binary version checks via systemd timers.

Optional service — comment the TRIVY dict in group_data/all.py to retire
it; the task then stops + disables both timers (binary + cache stay on
disk for rollback). NTFY is the alert sink: when it's retired the scans
still run on schedule but the ntfy pushes become no-ops (the `notify`
shell helper short-circuits on an empty URL).
"""

import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK
from tasks.util import optional

TRIVY = optional("TRIVY")
NTFY = optional("NTFY")


if TRIVY is None:
    # Retired: stop + disable the timers + on-demand path, keep the binary + DB
    # cache on disk.
    for _unit in ("trivy-cve-scan.timer", "trivy-cve-scan.path", "check-versions.timer"):
        systemd.service(
            name=f"Stop + disable {_unit}",
            service=_unit,
            running=False,
            enabled=False,
            daemon_reload=True,
        )
else:
    DOMAIN = NETWORK["domain"]
    # Empty when ntfy is retired — the notify() helper below then no-ops.
    NTFY_URL = f"https://ntfy.{DOMAIN}/{NTFY['topic']}" if NTFY else ""

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

    # --- CVE scan script ---
    # Scans all running container images for HIGH/CRITICAL CVEs. Two sinks from
    # one parse per image:
    #   1. Structured /var/lib/trivy/last-scan.json (assembled atomically at the
    #      end) — read by raspi-dashboard's /api/cve.
    #   2. One ntfy notification per affected image (skipped when NTFY_URL empty)
    #      — the optional digest.
    # Runs on the twice-weekly timer AND on demand via trivy-cve-scan.path
    # (raspi-dashboard touches /var/lib/trivy/scan-request).

    cve_scan = f"""\
#!/bin/bash
set -euo pipefail

NTFY_URL="{NTFY_URL}"
CACHE_DIR=/var/lib/trivy/cache
RESULT=/var/lib/trivy/last-scan.json
# /tmp is a 32M tmpfs — point trivy at the SD-backed dir for DB downloads.
export TMPDIR=/var/lib/trivy/tmp
export XDG_RUNTIME_DIR=/run
export DOCKER_HOST=unix:///run/podman/podman.sock

# Push to ntfy only when a sink is configured.
notify() {{ [ -n "$NTFY_URL" ] && curl -sf "$@" "$NTFY_URL" > /dev/null 2>&1 || true; }}

# Per-image structured fragments accumulate here, then merge into RESULT.
FRAGDIR=$(mktemp -d)
trap 'rm -rf "$FRAGDIR"' EXIT

# Drop --quiet so DB freshness/version lands in the journal — useful to
# diagnose silent-alert situations after a long gap between scans.
/usr/local/bin/trivy image \\
    --cache-dir "$CACHE_DIR" \\
    --download-db-only

_i=0
for image in $(podman ps --format "{{{{.Image}}}}" | sort -u); do
    json=$(/usr/local/bin/trivy image \\
            --cache-dir "$CACHE_DIR" \\
            --severity HIGH,CRITICAL \\
            --no-progress \\
            --quiet \\
            --format json \\
            "$image" 2>/dev/null) || true
    [ -z "$json" ] && continue

    # One python pass: write the image's JSON fragment (always, even when
    # clean) AND emit the ntfy digest (priority\\nmsg) on stdout (empty = clean).
    output=$(printf '%s' "$json" | IMAGE="$image" FRAG="$FRAGDIR/$_i.json" python3 -c '
import json, os, sys
data = json.load(sys.stdin)
crits, highs, vulns = [], [], []
for r in data.get("Results", []):
    for v in (r.get("Vulnerabilities") or []):
        sev = v["Severity"]
        cve = v["VulnerabilityID"]
        pkg = "{{}} {{}}".format(v["PkgName"], v.get("InstalledVersion", ""))
        title = v.get("Title", "")[:55]
        vulns.append({{"id": cve, "pkg": pkg, "severity": sev, "title": title}})
        line = "• {{}} — {{}} ({{}})".format(pkg, title, cve)
        (crits if sev == "CRITICAL" else highs).append(line)
with open(os.environ["FRAG"], "w") as f:
    json.dump({{"image": os.environ["IMAGE"], "critical": len(crits),
               "high": len(highs), "vulns": vulns}}, f)
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
    _i=$((_i + 1))

    [ -z "$output" ] && continue

    priority=$(printf '%s' "$output" | head -1)
    msg=$(printf '%s' "$output" | tail -n +2)
    short="${{image##*/}}"
    tags=warning
    [ "$priority" = urgent ] && tags=rotating_light

    notify \\
        -H "Title: CVE: ${{short}}" \\
        -H "Priority: $priority" \\
        -H "Tags: $tags" \\
        -d "$msg"
done

# Merge fragments into last-scan.json atomically (write-tmp + rename) so the
# dashboard never reads a half-written file.
SCANNED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ) FRAGDIR="$FRAGDIR" RESULT="$RESULT" python3 -c '
import glob, json, os
images = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(os.environ["FRAGDIR"], "*.json")))]
out = {{"scanned_at": os.environ["SCANNED_AT"], "images": images}}
tmp = os.environ["RESULT"] + ".tmp"
with open(tmp, "w") as f:
    json.dump(out, f)
os.replace(tmp, os.environ["RESULT"])
'
"""

    # --- Binary version check script (runs daily) ---
    # Checks Traefik and wg-portal against their latest GitHub releases.
    # Sends one ntfy notification per outdated binary (skipped when NTFY_URL empty).

    version_check = f"""\
#!/bin/bash
set -euo pipefail

NTFY_URL="{NTFY_URL}"

notify() {{ [ -n "$NTFY_URL" ] && curl -sf "$@" "$NTFY_URL" > /dev/null 2>&1 || true; }}

_latest() {{
    curl -sf "https://api.github.com/repos/$1/releases/latest" \\
        | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'])" 2>/dev/null || echo ""
}}

_check() {{
    local name="$1" installed="$2" repo="$3"
    local latest
    latest=$(_latest "$repo")
    if [ -n "$installed" ] && [ -n "$latest" ] && [ "$installed" != "$latest" ]; then
        notify \\
            -H "Title: Update Available" \\
            -H "Tags: arrow_up" \\
            -d "${{name}}: ${{installed}} → ${{latest}} — re-deploy to update"
    fi
}}

TRAEFIK_VER=$(/usr/local/bin/traefik version 2>/dev/null | awk '/Version:/ {{print "v"$2}}' || echo "")
_check "Traefik" "$TRAEFIK_VER" "traefik/traefik"

WGP_VER=$(/usr/local/bin/wg-portal --version 2>/dev/null | grep -oE 'v[0-9]+\\.[0-9]+\\.[0-9]+' | head -1 || echo "")
_check "wg-portal" "$WGP_VER" "h44z/wg-portal"
"""

    # --- Systemd units ---

    cve_service = f"""\
[Unit]
Description=Trivy CVE scan

[Service]
Type=oneshot
Environment=TMPDIR=/var/lib/trivy/tmp
# Cap the scan spike to trivy's own unit — MemorySwapMax=0 keeps it off the SD
# swap so a heavy scan is OOM-killed cleanly instead of thrashing the card.
MemoryMax={TRIVY["memory_max"]}
MemorySwapMax=0
ExecStart=/usr/local/bin/trivy-cve-scan.sh
"""

    # On-demand trigger: raspi-dashboard touches /var/lib/trivy/scan-request
    # (RW mount) → this path unit starts trivy-cve-scan.service. The file need
    # not pre-exist — systemd watches the parent dir until it appears.
    cve_path = """\
[Unit]
Description=Trigger Trivy CVE scan on dashboard request

[Path]
PathChanged=/var/lib/trivy/scan-request
Unit=trivy-cve-scan.service

[Install]
WantedBy=multi-user.target
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

    # Shared mount: owned by the raspi-dashboard container's uid (USER 1000 in
    # its Dockerfile) so the non-root container can touch scan-request and read
    # last-scan.json. The scan service runs as root and writes last-scan.json
    # regardless of owner.
    files.directory(
        name="Create /var/lib/trivy",
        path="/var/lib/trivy",
        user="1000",
        group="1000",
        mode="755",
        present=True,
    )

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
        ("/etc/systemd/system/trivy-cve-scan.path", cve_path, "644"),
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

    for unit in ("trivy-cve-scan.timer", "trivy-cve-scan.path", "check-versions.timer"):
        systemd.service(
            name=f"Enable {unit}",
            service=unit,
            enabled=True,
            running=True,
            daemon_reload=True,
        )
