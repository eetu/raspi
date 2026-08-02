"""Nib: Podman Quadlet container unit.

Direct-manipulation SVG path editor (../nib) — Rust core + SvelteKit SPA in one
image, all durable state in a single SQLite file under /var/lib/nib.

Optional service — comment the NIB dict in group_data/all.py to retire it; the
task then stops + disables the `nib` unit and leaves /var/lib/nib untouched for
rollback.

Two-deploy bootstrap for Kanidm OIDC — deploy 1 registers the `nib` client in
Kanidm and writes the generated secret to the vault; deploy 2 reads it back (via
tasks/secrets.py) and wires it into the container env. Until then nib runs but
nobody can sign in: unlike represent it has no forward-auth fallback, because it
is not behind oauth2-proxy (its /mcp surface is bearer-authed and would be 401'd
by an edge session gate).

Memory ceiling is higher than the other small apps: nib rasterizes SVG with resvg
to answer the MCP `render_document` tool, and holds an in-memory editor per open
project (evicted when idle).
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from tasks.util import optional

NIB = optional("NIB")


if NIB is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable nib (kept on disk for rollback)",
        service="nib",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _base_env = {
        "NIB_PORT": str(NIB["port"]),
        "NIB_DB": "sqlite:/data/nib.db",
        "NIB_DIST": "./dist",
    }

    def _env_line(k: str, v) -> str:
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{k}={escaped}"'

    _env_lines = "\n".join(_env_line(k, v) for k, v in {**_base_env, **NIB.get("env", {})}.items())

    quadlet = f"""\
[Unit]
Description=Nib — direct-manipulation SVG path editor
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=nib
Image={NIB["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/nib.env
Volume=/var/lib/nib:/data
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=256M
MemorySwapMax=64M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.directory(
        name="Create /var/lib/nib",
        path="/var/lib/nib",
        user="root",
        group="root",
        mode="777",
        present=True,
    )

    files.put(
        name="Write nib.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/nib.container",
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
        name="Start Nib",
        service="nib",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Nib if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.nib-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart nib
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Restart Nib if env changed",
        commands=[
            """
            ESTAMP=/etc/secrets/.nib-env-stamp
            ENV_HASH=$(sha256sum /etc/secrets/nib.env | cut -d' ' -f1)
            if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
              systemctl restart nib
              echo "$ENV_HASH" > "$ESTAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Nib image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {NIB["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' nib 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart nib
            fi
            """,
        ],
    )
