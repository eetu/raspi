"""Represent: Podman Quadlet container unit.

Markdown demo-script presenter (../represent) — Rust backend + SvelteKit SPA
in one image, all durable state in a single SQLite file under /var/lib/represent.

Optional service — comment the REPRESENT dict in group_data/all.py to retire
it; the task then stops + disables the `represent` unit and leaves
/var/lib/represent untouched for rollback.

Two-deploy bootstrap for Kanidm OIDC — deploy 1 registers the `represent`
client in Kanidm and writes the generated secret to the vault; deploy 2 reads
it back (via tasks/secrets.py) and wires it into the container env. Until then
represent accepts only oauth2-proxy forward-auth headers (DEV_AUTH is never
set in production).
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from tasks.util import optional

REPRESENT = optional("REPRESENT")


if REPRESENT is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable represent (kept on disk for rollback)",
        service="represent",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _base_env = {
        "REPRESENT_BIND": f"{REPRESENT['host']}:{REPRESENT['port']}",
        "REPRESENT_DB_PATH": "/data/represent.db",
    }

    def _env_line(k: str, v) -> str:
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{k}={escaped}"'

    _env_lines = "\n".join(
        _env_line(k, v) for k, v in {**_base_env, **REPRESENT.get("env", {})}.items()
    )

    quadlet = f"""\
[Unit]
Description=Represent — markdown demo-script presenter
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=represent
Image={REPRESENT["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/represent.env
Volume=/var/lib/represent:/data
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=128M
MemorySwapMax=64M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.directory(
        name="Create /var/lib/represent",
        path="/var/lib/represent",
        user="root",
        group="root",
        mode="777",
        present=True,
    )

    files.put(
        name="Write represent.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/represent.container",
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
        name="Start Represent",
        service="represent",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Represent if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.represent-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart represent
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Restart Represent if env changed",
        commands=[
            """
            ESTAMP=/etc/secrets/.represent-env-stamp
            ENV_HASH=$(sha256sum /etc/secrets/represent.env | cut -d' ' -f1)
            if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
              systemctl restart represent
              echo "$ENV_HASH" > "$ESTAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Represent image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {REPRESENT["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' represent 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart represent
            fi
            """,
        ],
    )
