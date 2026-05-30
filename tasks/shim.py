"""Shim: Podman Quadlet container unit.

Loopback-only Python sidecar wrapping mkb79/audible. Reachable only from
the scribe backend on the same host (Network=host + bind 127.0.0.1).
Audible auth state (RSA keys, cookies) lives on disk encrypted with a
passphrase from `/etc/secrets/shim.env` — never leaves this container,
never reachable from the LAN.

Optional service — comment the SHIM dict in group_data/all.py to retire
it. The task then drops into a cleanup branch that stops + disables the
systemd unit and leaves /var/lib/shim on disk for rollback. Shim is part
of the scribe bundle; retire scribe + shim + shelf together.
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from tasks.util import optional

SHIM = optional("SHIM")


if SHIM is None:
    # Retired: keep state on disk, just stop + disable the unit so the
    # container exits and the port is freed. /var/lib/shim is left
    # untouched — re-adding the SHIM block + redeploying restores the
    # service.
    systemd.service(
        name="Stop + disable shim (kept on disk for rollback)",
        service="shim",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:

    def _env_line(k: str, v) -> str:
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{k}={escaped}"'

    _base_env = {
        "SHIM_HOST": SHIM["host"],
        "SHIM_PORT": str(SHIM["port"]),
        "SHIM_DATA_DIR": "/data",
        "SHIM_RELOAD": "0",
        # glibc spawns up to 8×cores malloc arenas for a threaded process;
        # uvicorn + anyio's threadpool each grab one and hoard freed memory
        # (often 30–50% of RSS on ARM). Cap at 2. Must live in [Container] so
        # it's in the container process env before libc init reads it — a
        # [Service] Environment= would only reach the podman wrapper, and the
        # secrets dotenv is loaded too late.
        "MALLOC_ARENA_MAX": "2",
    }
    _env_lines = "\n".join(_env_line(k, v) for k, v in {**_base_env, **SHIM.get("env", {})}.items())

    quadlet = f"""\
[Unit]
Description=Shim — Audible auth + library + voucher sidecar
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=shim
Image={SHIM["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/shim.env
Volume=/var/lib/shim:/data
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryAccounting=yes
MemoryHigh=80M
MemoryMax=128M
MemorySwapMax=64M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.directory(
        name="Create /var/lib/shim",
        path="/var/lib/shim",
        user="root",
        group="root",
        # 0777 mirrors chat/halo's Quadlet pattern — the container runs as
        # USER 1000 (per the Dockerfile) and needs to write encrypted account
        # JSON files into /data, which bind-mounts here. Contents are encrypted
        # with SHIM_PASSPHRASE so the perm leak doesn't expose Audible session
        # state in cleartext.
        mode="777",
        present=True,
    )

    files.put(
        name="Write shim.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/shim.container",
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
        name="Start Shim",
        service="shim",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Shim if quadlet changed",
        commands=[
            f"""
        STAMP=/etc/containers/systemd/.shim-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart shim
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
        ],
    )

    server.shell(
        name="Restart Shim if env changed",
        commands=[
            """
        ESTAMP=/etc/secrets/.shim-env-stamp
        ENV_HASH=$(sha256sum /etc/secrets/shim.env | cut -d' ' -f1)
        if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
          systemctl restart shim
          echo "$ENV_HASH" > "$ESTAMP"
        fi
        """,
        ],
    )

    server.shell(
        name="Pull latest Shim image and restart if updated",
        commands=[
            f"""
        NEW=$(podman pull -q {SHIM["image"]})
        CUR=$(podman inspect --format '{{{{.Image}}}}' shim 2>/dev/null || echo "")
        if [ "$NEW" != "$CUR" ]; then
          systemctl restart shim
        fi
        """,
        ],
    )
