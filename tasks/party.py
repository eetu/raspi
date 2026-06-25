"""Party: Podman Quadlet container unit for the demoscene archive player.

The Rust backend image (`ghcr.io/eetu/scene-party`) embeds the SvelteKit SPA,
scans the `scene` CIFS share (PARTY_ROOT=/mnt/scene/parties), serves module /
image / video bytes + the WASM emulators (DOS/C64/Amiga), and owns a small
SQLite cache + derived-asset cache under /var/lib/party. Non-native image/video
is transcoded on demand via the loopback `transcoder` sidecar.

LAN-only, no oauth2-proxy: a single shared read-only archive with no per-user
state, so the human route is NOT in traefik's `_gated_hosts`. The container
runs with PARTY_OPEN=1 to skip the forward-auth header assertion; egress is
blocked in tasks/network_restrict.py.

The `scene` share is mounted READ-ONLY (`:ro`): party never writes the archive
(unlike tracker's rename feature). The Amiga Kickstart lives on the share at
parties/.support/kick40068.A1200 and is served read-only via /api/support.

Optional service — comment the PARTY dict in group_data/all.py to retire it.
The task then stops + disables the unit; /var/lib/party (the rebuildable cache)
stays on disk (the archive lives on the NAS regardless).
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS
from tasks.util import optional

PARTY = optional("PARTY")


if PARTY is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable party (kept on disk for rollback)",
        service="party",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _scene = CIFS["scene"]["mountpoint"]
    _mount_unit = f"{_scene.lstrip('/').replace('/', '-')}.automount"  # mnt-scene.automount

    quadlet = f"""\
[Unit]
Description=Party — demoscene archive player
After=network-online.target {_mount_unit}
Wants=network-online.target {_mount_unit}

[Container]
ContainerName=party
Image={PARTY["image"]}
Network=host
Volume=/var/lib/party:/data
Volume={_scene}:/mnt/scene:ro
Environment=PARTY_ROOT=/mnt/scene/parties
Environment=PARTY_SUPPORT_DIR=/mnt/scene/parties/.support
Environment=PARTY_DB_PATH=/data/party.db
Environment=PARTY_CACHE_DIR=/data/cache
Environment=PARTY_BIND={PARTY["host"]}:{PARTY["port"]}
# Non-native image/video transcoding via the loopback sidecar (tasks/transcoder.py).
Environment=PARTY_TRANSCODER_URL=http://127.0.0.1:3021
# LAN-only deploy with no oauth2-proxy in front — skip the forward-auth header
# assertion (the host is egress-restricted; see network_restrict.py).
Environment=PARTY_OPEN=1
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=256M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.directory(
        name="Create /var/lib/party",
        path="/var/lib/party",
        user="root",
        group="root",
        # Container runs as USER 1000 and writes the SQLite cache + derived
        # assets to /data.
        mode="777",
        present=True,
    )

    files.put(
        name="Write party.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/party.container",
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
        name="Start Party",
        service="party",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Party if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.party-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart party
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Party image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {PARTY["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' party 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart party
            fi
            """,
        ],
    )
