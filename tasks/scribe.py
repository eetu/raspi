"""Scribe: Podman Quadlet container unit.

The Rust backend image (`ghcr.io/eetu/scribe`) hosts the React UI, owns the
SQLite library DB, talks to the shim over loopback (:3004), and ships
ffmpeg jobs to scribe-press on the mini over TLS+bearer. Two-deploy
bootstrap mirrors chat: deploy 1 registers the `scribe` Kanidm client and
stashes the secret in BW; deploy 2 (via `tasks/secrets.py`) reads it and
wires it into `/etc/secrets/scribe.env`.

Audiobook trees live on the `audiobooks` CIFS share under `audible/`:
`books/` is audiobookshelf's source of truth, `originals/` is the
cold-storage AAXC tree scribe never lets ABS see.

Optional service — the self-hosted-audiobook stack (SCRIBE + SHIM + SHELF)
is all-or-none: comment all three dicts in group_data/all.py to retire it.
SCRIBE gates this task; if SCRIBE is commented it stops + disables the
`scribe` unit (state on /var/lib/scribe stays for rollback). scribe needs
the shim sidecar, so SHIM is expected present whenever SCRIBE is.
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS, NETWORK
from tasks.util import optional

SCRIBE = optional("SCRIBE")
SHIM = optional("SHIM")
AUDIOBOOKSHELF = optional("AUDIOBOOKSHELF")
SHELF = optional("SHELF")


if SCRIBE is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable scribe (kept on disk for rollback)",
        service="scribe",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    _audiobooks = CIFS["audiobooks"]["mountpoint"]
    _base_env = {
        "SCRIBE_BIND": f"{SCRIBE['host']}:{SCRIBE['port']}",
        "SCRIBE_DB_PATH": "/data/scribe.db",
        "SCRIBE_LIBRARY_DIR": f"{_audiobooks}/audible/books",
        "SCRIBE_ORIGINAL_DIR": f"{_audiobooks}/audible/originals",
        # On-disk cover cache under /var/lib/scribe (mounted at /data) so it
        # rides the existing restic backup of /var/lib/scribe.
        "SCRIBE_COVERS_DIR": "/data/covers",
    }

    # shim is scribe's Audible sidecar — part of the same bundle. Skip the URL
    # only if SHIM has been retired independently (scribe then loses Audible
    # import until shim is restored).
    if SHIM:
        _base_env["SCRIBE_SHIM_URL"] = f"http://{SHIM['host']}:{SHIM['port']}"

    # audiobookshelf rescan hook — backend POSTs after each completed job.
    # Drop both keys when ABS isn't configured so scribe skips the hook.
    if AUDIOBOOKSHELF:
        _base_env["ABS_URL"] = f"http://{AUDIOBOOKSHELF['host']}:{AUDIOBOOKSHELF['port']}"
        _base_env["ABS_LIBRARY_ID"] = AUDIOBOOKSHELF.get("scribe_library_id", "")

    # Surface shelf's public URL on scribe's UI so users can copy/paste it
    # into ABS-compatible clients. Auto-derived from SHELF + NETWORK — no
    # need to repeat the URL in group_data/all.py. SCRIBE["env"] still
    # overrides if set explicitly.
    if SHELF:
        _base_env["SCRIBE_SHELF_URL"] = f"https://{SHELF['url_prefix']}.{NETWORK['domain']}"

    def _env_line(k: str, v) -> str:
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{k}={escaped}"'

    _env_lines = "\n".join(
        _env_line(k, v) for k, v in {**_base_env, **SCRIBE.get("env", {})}.items()
    )

    quadlet = f"""\
[Unit]
Description=Scribe — self-hosted Audible mirror
After=network-online.target mnt-audiobooks.automount shim.service
Wants=network-online.target mnt-audiobooks.automount

[Container]
ContainerName=scribe
Image={SCRIBE["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/scribe.env
Volume=/var/lib/scribe:/data
Volume={_audiobooks}/audible:{_audiobooks}/audible
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
        name="Create /var/lib/scribe",
        path="/var/lib/scribe",
        user="root",
        group="root",
        mode="777",
        present=True,
    )

    # Pre-create the library + originals trees on the CIFS share so podman's
    # bind-mount can resolve the host path. CIFS rejects chmod/chown, so this
    # uses `files.directory` with only `path` + `present` — pyinfra skips the
    # call entirely when the dir already exists (proper idempotent gate).
    for _sub in ("audible", "audible/books", "audible/originals"):
        files.directory(
            name=f"Ensure {_audiobooks}/{_sub} on NAS",
            path=f"{_audiobooks}/{_sub}",
            present=True,
        )

    files.put(
        name="Write scribe.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/scribe.container",
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
        name="Start Scribe",
        service="scribe",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Scribe if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.scribe-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart scribe
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Restart Scribe if env changed",
        commands=[
            """
            ESTAMP=/etc/secrets/.scribe-env-stamp
            ENV_HASH=$(sha256sum /etc/secrets/scribe.env | cut -d' ' -f1)
            if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
              systemctl restart scribe
              echo "$ENV_HASH" > "$ESTAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Scribe image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {SCRIBE["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' scribe 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart scribe
            fi
            """,
        ],
    )
