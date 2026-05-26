"""Scribe-shelf: read-only ABS-compatible sidecar for external clients.

Mounts `/var/lib/scribe/scribe.db` and the audiobook library tree
read-only (`:ro` bind), exposes a tight Audiobookshelf API subset on
port 3006. Listen This and other ABS clients connect here directly,
bypassing the real audiobookshelf entirely.

Optional service — drop the SHELF dict from `group_data/all.py` (or
comment the `local.include("tasks/shelf.py")` in `deploy.py`) and the
container simply won't be deployed. scribe itself doesn't depend on
shelf for any of its own work.
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS, SHELF

_audiobooks = CIFS["audiobooks"]["mountpoint"]
_base_env = {
    "SHELF_BIND": f"{SHELF['host']}:{SHELF['port']}",
    "SHELF_DB_PATH": "/data/scribe.db",
    "SHELF_LIBRARY_DIR": f"{_audiobooks}/audible/books",
}


def _env_line(k: str, v) -> str:
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    escaped = v.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{k}={escaped}"'


_env_lines = "\n".join(_env_line(k, v) for k, v in {**_base_env, **SHELF.get("env", {})}.items())

# /var/lib/scribe holds scribe.db; mounted :ro into shelf so the
# read-only DB open in code is also enforced at the container boundary.
quadlet = f"""\
[Unit]
Description=Scribe-Shelf — read-only ABS-compatible view of scribe's library
After=network-online.target mnt-audiobooks.automount scribe.service
Wants=network-online.target mnt-audiobooks.automount

[Container]
ContainerName=shelf
Image={SHELF["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/shelf.env
Volume=/var/lib/scribe:/data:ro
Volume={_audiobooks}/audible/books:{_audiobooks}/audible/books:ro
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=120
MemoryMax=96M
MemorySwapMax=32M

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

files.put(
    name="Write shelf.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/shelf.container",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Reload quadlet units (shelf)",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true"
    ],
)

systemd.service(
    name="Start Shelf",
    service="shelf",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart Shelf if quadlet changed",
    commands=[
        f"""
        STAMP=/etc/containers/systemd/.shelf-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart shelf
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)

server.shell(
    name="Restart Shelf if env changed",
    commands=[
        """
        ESTAMP=/etc/secrets/.shelf-env-stamp
        ENV_HASH=$(sha256sum /etc/secrets/shelf.env | cut -d' ' -f1)
        if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
          systemctl restart shelf
          echo "$ENV_HASH" > "$ESTAMP"
        fi
        """,
    ],
)

server.shell(
    name="Pull latest Shelf image and restart if updated",
    commands=[
        f"""
        NEW=$(podman pull -q {SHELF["image"]})
        CUR=$(podman inspect --format '{{{{.Image}}}}' shelf 2>/dev/null || echo "")
        if [ "$NEW" != "$CUR" ]; then
          systemctl restart shelf
        fi
        """,
    ],
)
