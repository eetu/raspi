"""zot: self-hosted private OCI image registry (native Go binary).

LAN-only registry for container images you don't want to ship to a public
registry (even a private ghcr repo puts the bytes on GitHub). No auth — the
Traefik route is intentionally left out of `_gated_hosts`, so push from a dev
box over the LAN with `podman push registry.{domain}/app:tag`. The wildcard
cert is Let's Encrypt-trusted, so no client-side insecure-registry config.

Storage is the local SD ext4 (not the NAS): `dedupe` hardlinks identical blobs
across repos, which CIFS can't do, and a blob store wants atomic renames/locks
that CIFS handles poorly. `/var/lib/zot` is in RESTIC["paths"] for the off-box
NAS copy instead. GC + a keep-last-N-tags retention policy bound growth.

Optional service — comment the ZOT dict in group_data/all.py to retire it. The
task then drops into a cleanup branch that stops + disables the unit and leaves
the binary + blob store on disk, so re-adding the block restores the service.
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from tasks.util import optional, restart_if_changed

ZOT = optional("ZOT")


if ZOT is None:
    # Retired: keep binary + blob store on disk, just stop + disable the unit
    # so the port is freed. Re-adding the ZOT block + redeploying brings it back.
    systemd.service(
        name="Stop + disable zot (kept on disk for rollback)",
        service="zot",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    VERSION = ZOT["version"]
    BINARY_URL = f"https://github.com/project-zot/zot/releases/download/{VERSION}/zot-linux-arm64"

    # --- Binary (raw, not a tarball) ---

    server.shell(
        name=f"Install zot {VERSION}",
        commands=[
            f"""
            STAMP=/usr/local/bin/.zot-version
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
              curl -fsSL "{BINARY_URL}" -o /usr/local/bin/zot
              chmod +x /usr/local/bin/zot
              echo '{VERSION}' > "$STAMP"
            fi
            """,
        ],
    )

    # --- Data directory (blob store) ---

    files.directory(
        name="Create /var/lib/zot",
        path="/var/lib/zot",
        user="root",
        group="root",
        mode="700",
        present=True,
    )

    files.directory(
        name="Create /etc/zot",
        path="/etc/zot",
        user="root",
        group="root",
        mode="755",
        present=True,
    )

    # --- Config ---

    config = {
        "storage": {
            "rootDirectory": "/var/lib/zot",
            "dedupe": True,
            "gc": True,
            "gcDelay": "1h",
            "gcInterval": "24h",
            "retention": {
                "policies": [
                    {
                        "repositories": ["**"],
                        "deleteUntagged": True,
                        "keepTags": [{"mostRecentlyPushedCount": ZOT["keep_tags"]}],
                    }
                ]
            },
        },
        "http": {"address": ZOT["host"], "port": str(ZOT["port"])},
        "log": {"level": "info"},
    }
    config_json = json.dumps(config, indent=2)

    files.put(
        name="Write zot config",
        src=io.BytesIO(config_json.encode()),
        dest="/etc/zot/config.json",
        user="root",
        group="root",
        mode="644",
    )

    # --- systemd service ---

    service_unit = f"""\
[Unit]
Description=zot private OCI registry
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/zot serve /etc/zot/config.json
WorkingDirectory=/var/lib/zot
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax={ZOT["memory_max"]}
ProtectSystem=strict
ReadWritePaths=/var/lib/zot
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
"""

    files.put(
        name="Write zot systemd unit",
        src=io.BytesIO(service_unit.encode()),
        dest="/etc/systemd/system/zot.service",
        user="root",
        group="root",
        mode="644",
    )

    _unit_hash = hashlib.sha256((service_unit + config_json).encode()).hexdigest()

    systemd.service(
        name="Enable zot",
        service="zot",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart zot if unit or config changed",
        commands=[restart_if_changed("zot", _unit_hash)],
    )
