"""Tracker: Podman Quadlet container unit for the module-collection player.

The Rust backend image (`ghcr.io/eetu/tracker`) embeds the SvelteKit SPA,
scans the `mods` CIFS share, serves module bytes + the FT2 UI, and owns a
small SQLite cache (path index + libopenmpt metadata keyed by content hash)
under /var/lib/tracker.

LAN-only, no oauth2-proxy: the module library is a single shared read-only
collection with no per-user state, so the human route is NOT in traefik's
`_gated_hosts`. The container runs with `TRACKER_OPEN=1` to skip the
forward-auth header assertion; egress is blocked in tasks/network_restrict.py.

The `mods` share is mounted READ-WRITE (not :ro like navidrome's music): the
list view renames/moves modules in place (`/api/rename`) to clean up names
from old CD rips.

Optional service — comment the TRACKER dict in group_data/all.py to retire
it. The task then stops + disables the unit; /var/lib/tracker (the SQLite
cache) stays on disk for rollback (the modules live on the NAS regardless).
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS
from tasks.util import optional

TRACKER = optional("TRACKER")


if TRACKER is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable tracker (kept on disk for rollback)",
        service="tracker",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _mods = CIFS["mods"]["mountpoint"]
    _mount_unit = f"{_mods.lstrip('/').replace('/', '-')}.automount"  # mnt-mods.automount

    quadlet = f"""\
[Unit]
Description=Tracker — FastTracker 2-style module player
After=network-online.target {_mount_unit}
Wants=network-online.target {_mount_unit}

[Container]
ContainerName=tracker
Image={TRACKER["image"]}
Network=host
Volume=/var/lib/tracker:/data
Volume={_mods}:/mods
Environment=TRACKER_ROOT=/mods
Environment=TRACKER_DB_PATH=/data/tracker.db
Environment=TRACKER_BIND={TRACKER["host"]}:{TRACKER["port"]}
# LAN-only deploy with no oauth2-proxy in front — skip the forward-auth
# header assertion (the host is egress-restricted; see network_restrict.py).
Environment=TRACKER_OPEN=1
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=128M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.directory(
        name="Create /var/lib/tracker",
        path="/var/lib/tracker",
        user="root",
        group="root",
        # Container runs as USER 1000 and writes the SQLite cache to /data.
        mode="777",
        present=True,
    )

    files.put(
        name="Write tracker.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/tracker.container",
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
        name="Start Tracker",
        service="tracker",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Tracker if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.tracker-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart tracker
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Tracker image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {TRACKER["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' tracker 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart tracker
            fi
            """,
        ],
    )
