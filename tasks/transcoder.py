"""Transcoder: Podman Quadlet for the party media sidecar.

`ghcr.io/eetu/scene-transcoder` is a tiny Rust axum service (alpine + ffmpeg)
that the party backend calls over loopback: POST raw bytes + an `ext` hint, get
PNG/MP4 back (ILBM/PCX/TGA stills, MPEG/AVI/FLI video). Stateless — the party
backend owns the derived-asset cache — so there's no volume.

Loopback-only: PARTY_TRANSCODER_HOST=127.0.0.1 so even with `Network=host` it
binds only the loopback interface (party-backend is co-located on the Pi). No
oauth2/route — it's never web-exposed. Egress-blocked in network_restrict.py.

Co-located on the Pi for now to see how Pi-4 ffmpeg holds up; can move to a
beefier host later by repointing party's PARTY_TRANSCODER_URL.

Optional service — comment the TRANSCODER dict in group_data/all.py to retire
it (party then falls back to download links for non-native media).
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from tasks.util import optional

TRANSCODER = optional("TRANSCODER")


if TRANSCODER is None:
    systemd.service(
        name="Stop + disable transcoder",
        service="transcoder",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    quadlet = f"""\
[Unit]
Description=Party transcoder — ffmpeg media sidecar
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=transcoder
Image={TRANSCODER["image"]}
Network=host
# Loopback bind: only party-backend (same host) reaches it.
Environment=PARTY_TRANSCODER_HOST=127.0.0.1
Environment=PARTY_TRANSCODER_PORT={TRANSCODER["port"]}
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=120
MemoryMax={TRANSCODER.get("memory_max", "256M")}

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.put(
        name="Write transcoder.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/transcoder.container",
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
        name="Start Transcoder",
        service="transcoder",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Transcoder if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.transcoder-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart transcoder
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Transcoder image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {TRANSCODER["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' transcoder 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart transcoder
            fi
            """,
        ],
    )
