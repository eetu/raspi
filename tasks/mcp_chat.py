"""chat-mcp: Podman Quadlet for the Rust MCP bridge in front of chat.

Speaks streamable-HTTP MCP at `/mcp` on 127.0.0.1:{MCP_CHAT.port} and forwards
img2img / inpaint calls to the chat backend (CHAT) on the same Pi. Traefik
proxies `mcp-chat.{domain}` to it. The subdomain has a public DNS A record
pointing at the LAN IP, so it resolves anywhere but only LAN/VPN clients can
actually reach it.

Auth at both hops, sourced from `/etc/secrets/mcp-chat.env`:
- `CHAT_MCP_SERVER_KEY` gates inbound clients hitting /mcp (Bearer).
- `CHAT_MCP_API_KEY` is the Bearer this service sends to chat-backend
  /api/v1/* — must match `CHAT_MCP_API_KEY` in /etc/secrets/chat.env.

Optional service — comment the MCP_CHAT dict in group_data/all.py to retire
it. The task then drops into a cleanup branch that stops + disables the
systemd unit and leaves /var/lib/mcp-chat (and the `mcp-chat` BW item)
untouched, so re-adding the block + redeploying restores the service.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK
from tasks.util import optional

MCP_CHAT = optional("MCP_CHAT")
# mcp-chat is a bridge in front of chat — CHAT is optional, so a retired chat
# just leaves CHAT_BACKEND_URL unset (the bridge has nothing to forward to).
CHAT = optional("CHAT")


if MCP_CHAT is None:
    # Retired: keep state on disk, just stop + disable the unit so the
    # container exits and the port is freed.
    systemd.service(
        name="Stop + disable mcp-chat (kept on disk for rollback)",
        service="mcp-chat",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    env = {
        "CHAT_MCP_TRANSPORT": "http",
        # Image's Dockerfile default port (8090) collides with ntfy on the host —
        # override so the listener binds the slot reserved for mcp-chat.
        "PORT": str(MCP_CHAT["port"]),
        # Network=host means the listener is reachable from the LAN unless we
        # constrain it. Bind loopback only so Traefik is the sole entry point.
        "CHAT_MCP_BIND": "127.0.0.1",
        # URL agents see in tool results when fetching rendered images from the
        # backend's /api/v1/images/{uuid}.png buffer. Loopback is unreachable for
        # the agent, so point at the Traefik-fronted chat host instead.
        "CHAT_BACKEND_PUBLIC_URL": f"https://chat.{NETWORK['domain']}",
        "RUST_LOG": "chat_mcp=info",
    }
    if CHAT:
        env["CHAT_BACKEND_URL"] = f"http://{CHAT['host']}:{CHAT['port']}"
    _env_lines = "\n".join(f'Environment="{k}={v}"' for k, v in env.items())

    quadlet = f"""\
[Unit]
Description=chat-mcp — MCP bridge for chat img2img / inpaint
After=network-online.target chat.service
Wants=network-online.target chat.service

[Container]
ContainerName=mcp-chat
Image={MCP_CHAT["image"]}
Network=host
EnvironmentFile=/etc/secrets/mcp-chat.env
{_env_lines}
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=120
MemoryMax=128M
MemorySwapMax=64M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.put(
        name="Write mcp-chat.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/mcp-chat.container",
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
        name="Start mcp-chat",
        service="mcp-chat",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart mcp-chat if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.mcp-chat-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart mcp-chat
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Restart mcp-chat if env changed",
        commands=[
            """
            ESTAMP=/etc/secrets/.mcp-chat-env-stamp
            ENV_HASH=$(sha256sum /etc/secrets/mcp-chat.env | cut -d' ' -f1)
            if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
              systemctl restart mcp-chat
              echo "$ENV_HASH" > "$ESTAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest mcp-chat image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {MCP_CHAT["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' mcp-chat 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart mcp-chat
            fi
            """,
        ],
    )
