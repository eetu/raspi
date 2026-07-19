"""dice: Podman Quadlet container unit for the multiplayer dice roller.

The Rust backend image (`ghcr.io/eetu/dice`) embeds the SPA, serves the UI +
a WebSocket (`/ws`, same-origin), and keeps all room state in memory — no
backend store, no secrets, no state on disk. A restart drops every game by
design (rooms expire after DICE_TTL_SECS anyway).

Public/un-gated: anyone with a room code joins, so there is NO oauth2-proxy in
front (dice is deliberately NOT in traefik's `_gated_hosts`) and no auth layer
at all — unlike tracker/party there is no `_OPEN` flag to set. Network=host +
DICE_BIND on loopback keeps the raw backend off the LAN (Traefik is the only
public listener); egress is blocked in tasks/network_restrict.py.

Optional service — comment the DICE dict in group_data/all.py to retire it;
the task then stops + disables the unit. Stateless, nothing to keep.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from tasks.util import optional

DICE = optional("DICE")


if DICE is None:
    # Retired: stateless, so just stop + disable the unit.
    systemd.service(
        name="Stop + disable dice",
        service="dice",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    quadlet = f"""\
[Unit]
Description=dice — multiplayer dice roller (Rust + SPA)
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=dice
Image={DICE["image"]}
Network=host
# Bind loopback — Traefik is the only public listener. The image default is
# 0.0.0.0:{DICE["port"]}, which would expose the raw backend LAN-wide.
Environment=DICE_BIND={DICE["host"]}:{DICE["port"]}
Environment=DICE_TTL_SECS={DICE["ttl_secs"]}
Environment=DICE_MAX={DICE["max_dice"]}
Environment=DICE_MAX_ROOMS={DICE["max_rooms"]}
Environment=DICE_MAX_PLAYERS={DICE["max_players"]}
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax={DICE["memory_max"]}

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.put(
        name="Write dice.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/dice.container",
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
        name="Start dice",
        service="dice",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart dice if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.dice-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart dice
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest dice image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {DICE["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' dice 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart dice
            fi
            """,
        ],
    )
