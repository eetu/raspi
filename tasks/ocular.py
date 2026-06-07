"""ocular: native deploy of the camera-vision app to the camera node (raspo).

`camera` feature. v1 is a native systemd service (not a container): picamera2
reaches /dev/* directly, sidestepping libcamera-in-container passthrough.

The app is shipped as the self-contained release tarball published by ocular's
Release workflow (backend src + built SPA + VERSION), NOT from the local working
tree — so the deploy can never silently ship a stale `frontend/dist`. The Pi
pulls it directly: egress is restricted per-service (cgroup) in
tasks/network_restrict.py, not host-wide, so the deploy (running as root, not in
ocular.service's cgroup) reaches github.com fine. Channels:
  * "main"   → the rolling prerelease, refreshed on every push to main
  * "vX.Y.Z" → a pinned tag release
Set OCULAR["version"] (default "main"). The rolling `main` tag's *name* never
changes, so the published .sha256 — not a version string — gates re-download
and restart.

Layout on raspo:
  /opt/ocular/src    backend package (run via PYTHONPATH)   — from tarball
  /opt/ocular/dist   built Svelte SPA                        — from tarball
  /opt/ocular/VERSION  version + commit of the live build    — from tarball
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

from pyinfra.operations import files, server, systemd

from tasks.util import optional, restart_if_changed

OCULAR = optional("OCULAR")

# Runtime deps installed into the venv (system site-packages provides the rest).
_VENV_DEPS = "'fastapi>=0.136' 'uvicorn>=0.47'"

# Public repo publishing the release tarball (see ocular's .github/release.yaml).
_REPO = "eetu/ocular"


if OCULAR is None:
    systemd.service(
        name="Stop + disable ocular (kept on disk for rollback)",
        service="ocular",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _channel = OCULAR.get("version", "main")
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

    # Pull the release tarball (src + dist + VERSION) onto the Pi. The published
    # .sha256 gates the work: re-download + extract only when it differs from
    # what's on disk, and record the new hash to /opt/ocular/.release-sha — which
    # the restart step below fingerprints, so a fresh build triggers a restart.
    # `set -e` + `sha256sum -c` fail the deploy closed on a corrupt/MITM'd asset
    # rather than ship it. `-f` makes curl exit non-zero on an HTTP error (e.g. a
    # missing tag) instead of saving the error page as the tarball.
    _asset = f"ocular-{_channel}.tar.gz"
    _base = f"https://github.com/{_REPO}/releases/download/{_channel}"
    server.shell(
        name=f"Pull ocular release ({_channel})",
        commands=[
            f"""
            set -eu
            STAMP=/opt/ocular/.release-sha
            NEW=$(curl -fsSL "{_base}/{_asset}.sha256")
            if [ "$(cat "$STAMP" 2>/dev/null)" != "$NEW" ]; then
              curl -fsSL "{_base}/{_asset}" -o /tmp/ocular.tar.gz
              echo "$NEW  /tmp/ocular.tar.gz" | sha256sum -c -
              rm -rf /opt/ocular/src /opt/ocular/dist /opt/ocular/VERSION
              tar -xzf /tmp/ocular.tar.gz -C /opt/ocular
              rm -f /tmp/ocular.tar.gz
              echo "$NEW" > "$STAMP"
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

    # Restart when the unit or config (known at plan time) changes, OR when the
    # pulled release changes — the latter is only known at run time, so it rides
    # in as an env_file: restart_if_changed folds /opt/ocular/.release-sha's
    # content into the stamp, and the pull step above rewrites it on a fresh build.
    _static_hash = hashlib.sha256((service_unit + _config_json).encode()).hexdigest()

    systemd.service(
        name="Enable ocular",
        service="ocular",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart ocular if release/config/unit changed",
        commands=[
            restart_if_changed("ocular", _static_hash, env_files=["/opt/ocular/.release-sha"])
        ],
    )
