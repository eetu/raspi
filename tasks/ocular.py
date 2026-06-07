"""ocular: native deploy of the camera-vision app to the camera node (raspo).

`camera` feature. v1 is a native systemd service (not a container): picamera2
reaches /dev/* directly, sidestepping libcamera-in-container passthrough. The
app ships as a self-contained tarball (backend src + built SPA) published to
GitHub releases by ../ocular's release workflow — set OCULAR["version"] to a
channel: "main" tracks the branch (rebuilt each push), or pin "vX.Y.Z". The Pi
pulls it directly (repo is public; the LAN-only egress block is scoped to the
running ocular.service cgroup, not this deploy shell).

Layout on raspo:
  /opt/ocular/src    backend package (run via PYTHONPATH), from the tarball
  /opt/ocular/dist   built Svelte SPA, from the tarball
  /opt/ocular/.venv  venv (--system-site-packages → picamera2/numpy/Pillow from
                     apt; fastapi/uvicorn from pip)
  /etc/ocular/config.json   rendered from the OCULAR dict (camera + detectors)
  /var/lib/ocular    state (revolution history DB — ocular.db)

Egress: "ocular" is in tasks/network_restrict.py RESTRICTED (LAN-only). The hub
port is opened to the LAN by tasks/hardening.py only on the camera host.
"""

import hashlib
import io
import json

from pyinfra import host
from pyinfra.operations import files, server, systemd

from tasks.util import optional, restart_if_changed

OCULAR = optional("OCULAR")

# ocular is camera-node-only. deploy.py already gates the task by the `camera`
# feature, but running this file directly (`pyinfra inventory.py tasks/ocular.py`)
# bypasses that gating — and since OCULAR is a global in all.py (never None), the
# install block would otherwise run on every targeted host. Guard on the host's
# own FEATURES so a direct run is a no-op anywhere but the camera node.
_is_camera_node = "camera" in set(host.data.get("FEATURES") or ())

# Runtime deps installed into the venv (system site-packages provides the rest).
_VENV_DEPS = "'fastapi>=0.136' 'uvicorn>=0.47'"


if OCULAR is None:
    systemd.service(
        name="Stop + disable ocular (kept on disk for rollback)",
        service="ocular",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
elif not _is_camera_node:
    # Not the camera node — do nothing (never install ocular here).
    pass
else:
    VERSION = OCULAR.get("version", "main")
    BUNDLE_URL = (
        f"https://github.com/eetu/ocular/releases/download/{VERSION}/ocular-{VERSION}.tar.gz"
    )

    _config = {"camera": OCULAR["camera"], "detectors": {"revolution": OCULAR["revolution"]}}
    _config_json = json.dumps(_config, indent=2)
    _deps_hash = hashlib.sha256(_VENV_DEPS.encode()).hexdigest()

    for _path in ("/opt/ocular", "/etc/ocular", "/var/lib/ocular"):
        files.directory(
            name=f"Create {_path}",
            path=_path,
            user="root",
            group="root",
            mode="755",
            present=True,
        )

    # Pull the published tarball (src + built SPA) on the Pi. Keyed on the asset's
    # sha256: a fresh "main" build is picked up and the service restarted; a pinned
    # vX.Y.Z is a no-op once installed. The restart is fail-soft so the very first
    # deploy (no unit yet) still extracts — systemd.service below then starts it.
    server.shell(
        name=f"Fetch + extract ocular bundle ({VERSION})",
        commands=[
            f"""
            set -e
            SUM=$(curl -fsSL "{BUNDLE_URL}.sha256")
            STAMP=/opt/ocular/.bundle-sha256
            if [ "$(cat "$STAMP" 2>/dev/null)" != "$SUM" ]; then
              TMP=$(mktemp)
              curl -fsSL "{BUNDLE_URL}" -o "$TMP"
              echo "$SUM  $TMP" | sha256sum -c -
              rm -rf /opt/ocular/src /opt/ocular/dist
              tar -xzf "$TMP" -C /opt/ocular
              rm -f "$TMP"
              echo "$SUM" > "$STAMP"
              systemctl restart ocular 2>/dev/null || true
            fi
            """,
        ],
    )

    files.put(
        name="Write ocular config.json",
        src=io.BytesIO(_config_json.encode()),
        dest="/etc/ocular/config.json",
        user="root",
        group="root",
        mode="644",
    )

    server.shell(
        name="Create ocular venv + install runtime deps",
        commands=[
            f"""
            if [ ! -x /opt/ocular/.venv/bin/python ]; then
              python3 -m venv --system-site-packages /opt/ocular/.venv
            fi
            STAMP=/opt/ocular/.venv/.deps-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_deps_hash}" ]; then
              /opt/ocular/.venv/bin/pip install --upgrade {_VENV_DEPS}
              echo '{_deps_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    # Camera access is the fiddly part of sandboxing this. If libcamera fails to
    # open the sensor (check `journalctl -u ocular`), relax DevicePolicy or add
    # the missing /dev node here. SupplementaryGroups=video grants the device
    # node group; the DeviceAllow list covers the Pi camera stack.
    service_unit = f"""\
[Unit]
Description=ocular camera-vision app
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=PYTHONPATH=/opt/ocular/src
Environment=OCULAR_BIND=0.0.0.0:{OCULAR["port"]}
Environment=OCULAR_STATIC_DIR=/opt/ocular/dist
Environment=OCULAR_CONFIG=/etc/ocular/config.json
Environment=OCULAR_STATE_DIR=/var/lib/ocular
ExecStart=/opt/ocular/.venv/bin/python -m ocular
Restart=always
RestartSec=5
MemoryMax=256M
SupplementaryGroups=video
DeviceAllow=/dev/vchiq rw
DeviceAllow=/dev/vcsm-cma rw
DeviceAllow=char-media rw
DeviceAllow=char-video4linux rw
DeviceAllow=/dev/dma_heap/system rw
DeviceAllow=/dev/dma_heap/linux,cma rw
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=/var/lib/ocular /etc/ocular
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
"""

    files.put(
        name="Write ocular systemd unit",
        src=io.BytesIO(service_unit.encode()),
        dest="/etc/systemd/system/ocular.service",
        user="root",
        group="root",
        mode="644",
    )

    # Restart on a unit/config/version change. Bundle *content* changes are
    # handled by the fetch step above (it restarts when the asset sha changes),
    # which is what makes the rolling "main" channel pick up each new build.
    _static_hash = hashlib.sha256((service_unit + _config_json + VERSION).encode()).hexdigest()

    systemd.service(
        name="Enable ocular",
        service="ocular",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart ocular if config/unit/version changed",
        commands=[restart_if_changed("ocular", _static_hash)],
    )
