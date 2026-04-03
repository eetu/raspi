"""wg-portal: download binary, write config, systemd service."""

import io

from pyinfra.operations import files, server, systemd

import secrets as bw
from group_data.all import NETWORK, WGPORTAL, WIREGUARD

VERSION = WGPORTAL["version"]
BINARY_URL = f"https://github.com/h44z/wg-portal/releases/download/{VERSION}/wg-portal_linux_arm64"

# --- Binary ---

server.shell(
    name=f"Install wg-portal {VERSION}",
    commands=[
        f'INSTALLED=$(/usr/local/bin/wg-portal --version 2>/dev/null | grep -o "{VERSION}" || true)',
        f'if [ "$INSTALLED" != "{VERSION}" ]; then',
        f'  curl -fsSL "{BINARY_URL}" -o /usr/local/bin/wg-portal',
        "  chmod +x /usr/local/bin/wg-portal",
        "fi",
    ],
)

# --- Config ---

creds = bw.wg_portal_creds()

config_yaml = f"""core:
  admin_user:     "{creds["username"]}"
  admin_password: "{creds["password"]}"

web:
  listening_address: "{WGPORTAL["host"]}:{WGPORTAL["port"]}"
  external_url:      "https://vpn.{NETWORK["domain"]}"

database:
  type: sqlite
  dsn:  /etc/wg-portal/wg-portal.db

wireguard:
  managed_interfaces:
    - wg0
  default_interface_config:
    dns_servers:
      - "{WIREGUARD["ip"]}"
"""

files.directory(
    name="Create /etc/wg-portal",
    path="/etc/wg-portal",
    user="root",
    group="root",
    mode="750",
    present=True,
)

files.put(
    name="Write wg-portal config",
    src=io.BytesIO(config_yaml.encode()),
    dest="/etc/wg-portal/config.yaml",
    user="root",
    group="root",
    mode="600",
)

# --- systemd service ---

service_unit = """\
[Unit]
Description=WireGuard Portal
After=network-online.target wg-quick@wg0.service
Wants=network-online.target
Requires=wg-quick@wg0.service

[Service]
Type=simple
ExecStart=/usr/local/bin/wg-portal serve --config /etc/wg-portal/config.yaml
Restart=always
RestartSec=5
AmbientCapabilities=CAP_NET_ADMIN

[Install]
WantedBy=multi-user.target
"""

files.put(
    name="Write wg-portal systemd unit",
    src=io.BytesIO(service_unit.encode()),
    dest="/etc/systemd/system/wg-portal.service",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable wg-portal",
    service="wg-portal",
    enabled=True,
    running=True,
    daemon_reload=True,
)
