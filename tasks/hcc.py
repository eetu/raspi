"""HCC: Podman Quadlet container unit."""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from group_data.all import HCC

_base_env = {
    "PORT": str(HCC["port"]),
    "HOSTNAME": HCC["host"],
    "HCC_DB_PATH": "/data/hcc.db",
}


def _env_line(k: str, v) -> str:
    # Non-strings (dicts/lists/numbers/bools) → compact JSON. systemd.exec(5):
    # Environment= splits on whitespace unless value is double-quoted; inside
    # the quotes \" and \\ are the only escapes.
    if not isinstance(v, str):
        v = json.dumps(v, ensure_ascii=False)
    escaped = v.replace("\\", "\\\\").replace('"', '\\"')
    return f'Environment="{k}={escaped}"'


_env_lines = "\n".join(_env_line(k, v) for k, v in {**_base_env, **HCC["env"]}.items())

quadlet = f"""\
[Unit]
Description=HCC Dashboard
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=hcc
Image={HCC["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/hcc.env
Volume=/var/lib/hcc:/data
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=96M
MemorySwapMax=64M

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

files.directory(
    name="Create /var/lib/hcc",
    path="/var/lib/hcc",
    user="root",
    group="root",
    mode="777",
    present=True,
)

files.directory(
    name="Create /etc/containers/systemd",
    path="/etc/containers/systemd",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write hcc.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/hcc.container",
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
    name="Start HCC",
    service="hcc",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart HCC if quadlet changed",
    commands=[
        f"""
        STAMP=/etc/containers/systemd/.hcc-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart hcc
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)

server.shell(
    name="Restart HCC if env changed",
    commands=[
        """
        ESTAMP=/etc/secrets/.hcc-env-stamp
        ENV_HASH=$(sha256sum /etc/secrets/hcc.env | cut -d' ' -f1)
        if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
          systemctl restart hcc
          echo "$ENV_HASH" > "$ESTAMP"
        fi
        """,
    ],
)

server.shell(
    name="Pull latest HCC image and restart if updated",
    commands=[
        f"""
        NEW=$(podman pull -q {HCC["image"]})
        CUR=$(podman inspect --format '{{{{.Image}}}}' hcc 2>/dev/null || echo "")
        if [ "$NEW" != "$CUR" ]; then
          systemctl restart hcc
        fi
        """,
    ],
)
