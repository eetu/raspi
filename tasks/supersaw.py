"""supersaw: Podman Quadlet container unit.

Browser synth (../supersaw) — a static SvelteKit SPA served by nginx, all
audio client-side Web Audio. No backend, no secrets, no state on disk.

Optional service — comment the SUPERSAW dict in group_data/all.py to retire
it; the task then stops + disables the unit. Stateless, nothing to keep.

Behind oauth2-proxy (gated host in tasks/traefik.py) — no own login. nginx
serves an unauthenticated /status for gatus via the monitor router.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from tasks.util import optional

SUPERSAW = optional("SUPERSAW")


if SUPERSAW is None:
    # Retired: stateless, so just stop + disable the unit.
    systemd.service(
        name="Stop + disable supersaw",
        service="supersaw",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    # nginx listens on the host port directly (Network=host); the port is baked
    # into the image's nginx.conf — keep SUPERSAW["port"] in sync with it.
    quadlet = f"""\
[Unit]
Description=supersaw — browser synth (static SPA, nginx)
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=supersaw
Image={SUPERSAW["image"]}
Network=host
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax={SUPERSAW["memory_max"]}
MemorySwapMax=32M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.put(
        name="Write supersaw.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/supersaw.container",
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
        name="Start supersaw",
        service="supersaw",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart supersaw if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.supersaw-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart supersaw
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest supersaw image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {SUPERSAW["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' supersaw 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart supersaw
            fi
            """,
        ],
    )
