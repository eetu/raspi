"""chat-mcp: Podman Quadlet for the Rust MCP bridge in front of chat.

Speaks streamable-HTTP MCP at `/mcp` on 127.0.0.1:{MCP_CHAT.port} and forwards
img2img / inpaint calls to the chat backend (CHAT) on the same Pi. Traefik
proxies `mcp-chat.{domain}` to it, but only LAN/VPN clients can resolve that
name (the subdomain is in INTERNAL_SUBDOMAINS, so Cloudflare has no record).

Auth is opt-in:
- `CHAT_MCP_SERVER_KEY` (this service) gates inbound /mcp Bearer auth.
- `CHAT_MCP_API_KEY` (chat backend) gates the MCP→backend hop.
Both are unset for now — LAN perimeter is the only auth layer.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import CHAT, MCP_CHAT

env = {
    "CHAT_MCP_TRANSPORT": "http",
    # Image's Dockerfile default port (8090) collides with ntfy on the host —
    # override so the listener binds the slot reserved for mcp-chat.
    "PORT": str(MCP_CHAT["port"]),
    # Network=host means the listener is reachable from the LAN unless we
    # constrain it. Bind loopback only so Traefik is the sole entry point.
    "CHAT_MCP_BIND": "127.0.0.1",
    "CHAT_BACKEND_URL": f"http://{CHAT['host']}:{CHAT['port']}",
    "RUST_LOG": "chat_mcp=info",
    # Upstream bug: chat-mcp's BackendConfig::from_env() hard-requires this
    # env var even though chat-backend's mcp_api_key is `Option<String>` and
    # accepts unauthenticated calls when None. An empty value satisfies
    # `std::env::var`, and the resulting `Authorization: Bearer ` is ignored
    # by the backend. Drop this entry once the client is patched to make
    # api_key optional too.
    "CHAT_MCP_API_KEY": "",
}
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
