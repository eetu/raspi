"""raspi-dashboard: Podman Quadlet container unit.

Stateless fan-in of gatus health + beszel metrics + trivy CVE status onto one
LAN-only page. Optional service — comment the RASPI_DASHBOARD dict in
group_data/all.py to retire it; the task then stops + disables the unit. No
state on disk (no DB), so nothing to keep for rollback.

Behind oauth2-proxy (gated host in tasks/traefik.py) — no own login, no Kanidm
OIDC client. Reads:
  - gatus loopback REST API (unauthenticated on 127.0.0.1:3001 since Phase A),
  - beszel PocketBase (a dedicated read-only user, creds from the
    raspi-dashboard.env written by tasks/secrets.py),
  - /var/lib/trivy/last-scan.json (RW mount: it also touches scan-request to
    trigger an on-demand scan via the trivy-cve-scan.path unit).
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import BESZEL, GATUS
from tasks.util import optional

RASPI_DASHBOARD = optional("RASPI_DASHBOARD")


if RASPI_DASHBOARD is None:
    # Retired: stateless, so just stop + disable the unit.
    systemd.service(
        name="Stop + disable raspi-dashboard",
        service="raspi-dashboard",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _env = {
        "DASHBOARD_BIND": f"{RASPI_DASHBOARD['host']}:{RASPI_DASHBOARD['port']}",
        "GATUS_URL": f"http://{GATUS['host']}:{GATUS['port']}",
        "BESZEL_URL": f"http://{BESZEL['host']}:{BESZEL['port']}",
        "TRIVY_SCAN_FILE": "/var/lib/trivy/last-scan.json",
        "TRIVY_SCAN_REQUEST": "/var/lib/trivy/scan-request",
        # Cap glibc per-thread arenas — the house default for the 1GB Pi, keeps
        # idle RSS down so the container fits its 96M MemoryMax.
        "MALLOC_ARENA_MAX": "2",
    }
    _env_lines = "\n".join(f"Environment={k}={v}" for k, v in _env.items())

    quadlet = f"""\
[Unit]
Description=raspi-dashboard — service health + metrics + CVE status
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=raspi-dashboard
Image={RASPI_DASHBOARD["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/raspi-dashboard.env
# RW so the backend can read last-scan.json and touch scan-request (the
# trivy-cve-scan.path unit watches it). Rootful quadlet → container root can
# write the host-shared mount without extra privilege.
Volume=/var/lib/trivy:/var/lib/trivy
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax={RASPI_DASHBOARD["memory_max"]}
MemorySwapMax=32M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.put(
        name="Write raspi-dashboard.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/raspi-dashboard.container",
        user="root",
        group="root",
        mode="644",
    )

    server.shell(
        name="Reload quadlet units",
        commands=[
            "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true"
        ],
    )

    systemd.service(
        name="Start raspi-dashboard",
        service="raspi-dashboard",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart raspi-dashboard if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.raspi-dashboard-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart raspi-dashboard
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Restart raspi-dashboard if env changed",
        commands=[
            """
            ESTAMP=/etc/secrets/.raspi-dashboard-env-stamp
            ENV_HASH=$(sha256sum /etc/secrets/raspi-dashboard.env | cut -d' ' -f1)
            if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
              systemctl restart raspi-dashboard
              echo "$ENV_HASH" > "$ESTAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest raspi-dashboard image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {RASPI_DASHBOARD["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' raspi-dashboard 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart raspi-dashboard
            fi
            """,
        ],
    )
