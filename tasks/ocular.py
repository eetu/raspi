"""ocular: native deploy of the camera-vision app to the camera node (raspo).

`camera` feature. v1 is a native systemd service (not a container): picamera2
reaches /dev/* directly, sidestepping libcamera-in-container passthrough. The
app source is shipped from the sibling ../ocular working tree — build the
frontend first (`cd ../ocular/frontend && yarn build`); this task raises if
dist/ is missing rather than ship a backend with no UI.

Layout on raspo:
  /opt/ocular/src    backend package (run via PYTHONPATH)
  /opt/ocular/dist   built Svelte SPA
  /opt/ocular/.venv  venv (--system-site-packages → picamera2/numpy/Pillow from
                     apt; fastapi/uvicorn from pip)
  /etc/ocular/config.json   rendered from the OCULAR dict (camera + detectors)
  /var/lib/ocular    state (revolution count)

Egress: "ocular" is in tasks/network_restrict.py RESTRICTED (LAN-only). The hub
port is opened to the LAN by tasks/hardening.py only on the camera host.
"""

import hashlib
import io
import json
from pathlib import Path

from pyinfra.operations import files, server, systemd

from tasks.util import optional, restart_if_changed

OCULAR = optional("OCULAR")

# Runtime deps installed into the venv (system site-packages provides the rest).
_VENV_DEPS = "'fastapi>=0.136' 'uvicorn>=0.47'"


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


if OCULAR is None:
    systemd.service(
        name="Stop + disable ocular (kept on disk for rollback)",
        service="ocular",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _ocular_repo = Path(__file__).resolve().parents[2] / "ocular"
    _src = _ocular_repo / "backend" / "src"
    _dist = _ocular_repo / "frontend" / "dist"

    if not (_dist / "index.html").is_file():
        raise RuntimeError(
            f"ocular dist not built at {_dist} — run `cd {_ocular_repo}/frontend && yarn build` first"
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

    files.sync(
        name="Sync ocular backend src",
        src=str(_src),
        dest="/opt/ocular/src",
        delete=True,
        user="root",
        group="root",
    )

    files.sync(
        name="Sync ocular SPA dist",
        src=str(_dist),
        dest="/opt/ocular/dist",
        delete=True,
        user="root",
        group="root",
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
ReadWritePaths=/var/lib/ocular
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

    # Restart on any change to the unit, config, backend src, or built dist.
    _static_hash = hashlib.sha256(
        (service_unit + _config_json + _tree_hash(_src) + _tree_hash(_dist)).encode()
    ).hexdigest()

    systemd.service(
        name="Enable ocular",
        service="ocular",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart ocular if code/config/unit changed",
        commands=[restart_if_changed("ocular", _static_hash)],
    )
