"""Tracker: Podman Quadlet container unit for the module-collection player.

The Rust backend image (`ghcr.io/eetu/tracker`) embeds the SvelteKit SPA,
scans the `mods` CIFS share, serves module bytes + the FT2 UI, and owns a
small SQLite cache (path index + libopenmpt metadata keyed by content hash)
under /var/lib/tracker.

LAN-only, no oauth2-proxy: the module library is a single shared read-only
collection with no per-user state, so the human route is NOT in traefik's
`_gated_hosts`. The container runs with `TRACKER_OPEN=1` to skip the
forward-auth header assertion; egress is blocked in tasks/network_restrict.py.

The modules live under `mods/` on the `scene` share (one CIFS mount, no
separate `mods` mount) and the bind is READ-WRITE (not :ro like navidrome's
music): the list view renames/moves modules in place (`/api/rename`) to
clean up names from old CD rips. party's bind of the same mount stays :ro.

The High Voltage SID Collection comes in as a second root from the `scene`
share, read-only. It is never walked: tracker indexes it from the collection's
own `DOCUMENTS/Songlengths.md5`, so 61k tunes cost one 5MB read rather than the
minutes a stat-and-hash pass over CIFS would take. The C64 ROMs SID playback
wants live beside the modules in `mods/.support` — copyrighted and
operator-supplied, like the Amiga Kickstart under `parties/.support`.

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
    # Modules and HVSC both live on the single `scene` mount; the container
    # still sees them at /mods and /hvsc, so tracker.db paths never change.
    # Without the automount dep the container can start before the mount is
    # up, and podman fails the whole unit with `statfs …: host is down`
    # rather than degrading.
    _scene = CIFS["scene"]["mountpoint"]
    _mods = f"{_scene}/mods"
    _mount_unit = f"{_scene.lstrip('/').replace('/', '-')}.automount"  # mnt-scene.automount
    _hvsc = f"{_scene}/C64Music"
    _scene_unit = f"{_scene.lstrip('/').replace('/', '-')}.automount"  # mnt-scene.automount

    quadlet = f"""\
[Unit]
Description=Tracker — FastTracker 2-style module player
After=network-online.target {_mount_unit} {_scene_unit}
Wants=network-online.target {_mount_unit} {_scene_unit}

[Container]
ContainerName=tracker
Image={TRACKER["image"]}
Network=host
Volume=/var/lib/tracker:/data
Volume={_mods}:/mods
# The High Voltage SID Collection, read-only: tracker indexes it from the
# collection's own DOCUMENTS/Songlengths.md5 and never writes to it, so `:ro`
# states that and protects 61k tunes from a bug.
Volume={_hvsc}:/hvsc:ro
# Two roots: the module collection (walked + hashed) and HVSC (indexed from its
# own catalogue in seconds, no walk). Supersedes TRACKER_ROOT.
Environment=TRACKER_ROOTS=mods:scan:/mods,hvsc:hvsc:/hvsc
Environment=TRACKER_DB_PATH=/data/tracker.db
Environment=TRACKER_BIND={TRACKER["host"]}:{TRACKER["port"]}
# C64 ROMs for SID playback, beside the modules on the share (a dot-directory,
# so the scanner skips it) — same arrangement as the Amiga Kickstart under
# parties/.support. Operator-supplied and copyrighted, hence on the NAS rather
# than in the image. Without them a BASIC-driven RSID plays as near-silence.
Environment=TRACKER_ROMS_DIR=/mods/.support
# The scan is latency-bound on CIFS, not CPU-bound, so more threads than cores
# helps — but each one holds a read buffer, and this unit has a hard memory cap.
# 8 keeps nearly all of the measured win (8/16/32 were indistinguishable) at half
# the in-flight memory of the cores*4 default this Pi would otherwise pick.
Environment=TRACKER_SCAN_THREADS=8
# LAN-only deploy with no oauth2-proxy in front — skip the forward-auth
# header assertion (the host is egress-restricted; see network_restrict.py).
Environment=TRACKER_OPEN=1
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
# Raised from 128M when HVSC was added: the index writes 61,157 file rows plus
# 87,868 song rows in one transaction, and the cold module scan already peaked
# at ~115M of the old cap.
MemoryMax=256M

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
