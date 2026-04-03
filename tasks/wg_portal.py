"""wg-portal: download binary, write config, systemd service."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import NETWORK, WGPORTAL, WIREGUARD

VERSION = WGPORTAL["version"]
BINARY_URL = f"https://github.com/h44z/wg-portal/releases/download/{VERSION}/wg-portal_linux_arm64"

# --- Binary ---

server.shell(
    name=f"Install wg-portal {VERSION}",
    commands=[
        f"""
        INSTALLED=$(/usr/local/bin/wg-portal --version 2>/dev/null | grep -o "{VERSION}" || true)
        if [ "$INSTALLED" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" -o /usr/local/bin/wg-portal
          chmod +x /usr/local/bin/wg-portal
        fi
        """,
    ],
)

# --- Config ---

creds = bw.wg_portal_creds()

config_yaml = f"""core:
  admin_user:      "{creds["username"]}"
  admin_password:  "{creds["password"]}"
  admin_api_token: "{creds["api_token"]}"

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
    peer_defaults:
      allowed_ips:
        - "0.0.0.0/0"
        - "::/0"
"""

for path in ("/etc/wg-portal", "/etc/wg-portal/config"):
    files.directory(
        name=f"Create {path}",
        path=path,
        user="root",
        group="root",
        mode="750",
        present=True,
    )

files.put(
    name="Write wg-portal config",
    src=io.BytesIO(config_yaml.encode()),
    dest="/etc/wg-portal/config/config.yml",
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
WorkingDirectory=/etc/wg-portal
ExecStart=/usr/local/bin/wg-portal serve
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

_config_hash = hashlib.sha256((config_yaml + service_unit).encode()).hexdigest()

systemd.service(
    name="Enable wg-portal",
    service="wg-portal",
    enabled=True,
    running=True,
    daemon_reload=True,
)


server.shell(
    name="Restart wg-portal if config changed",
    commands=[
        f"""
        STAMP=/etc/wg-portal/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_config_hash}" ]; then
          systemctl restart wg-portal
          echo '{_config_hash}' > "$STAMP"
        fi
        """,
    ],
)

_wg_base_url = f"http://{WGPORTAL['host']}:{WGPORTAL['port']}"
_wg_auth = f"{creds['username']}:{creds['api_token']}"
_wg_endpoint = f"wg.{NETWORK['domain']}:{WIREGUARD['port']}"
_wg_dns = WIREGUARD["ip"]

server.shell(
    name="Set wg0 PeerDefEndpoint via API",
    commands=[
        f"""
        BASE_URL="{_wg_base_url}"
        AUTH="{_wg_auth}"
        ENDPOINT="{_wg_endpoint}"
        DNS="{_wg_dns}"

        for i in $(seq 1 15); do
          STATUS=$(curl -s -o /dev/null -w '%{{http_code}}' \
            -u "$AUTH" "$BASE_URL/api/v1/interface/by-id/wg0" 2>/dev/null || true)
          if [ "$STATUS" = "200" ]; then break; fi
          sleep 2
        done

        IFACE=$(curl -sf -u "$AUTH" "$BASE_URL/api/v1/interface/by-id/wg0" 2>/dev/null || true)
        if [ -z "$IFACE" ]; then echo "wg-portal: failed to get interface" >&2; exit 1; fi

        CURRENT_EP=$(echo "$IFACE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('PeerDefEndpoint',''))")
        if [ "$CURRENT_EP" = "$ENDPOINT" ]; then exit 0; fi

        IFACE=$(echo "$IFACE" | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['PeerDefEndpoint'] = '$ENDPOINT'
d['PeerDefDns'] = ['$DNS']
print(json.dumps(d))
")

        curl -sf -X PUT "$BASE_URL/api/v1/interface/by-id/wg0" \
          -H "Content-Type: application/json" \
          -u "$AUTH" \
          -d "$IFACE" >/dev/null
        """,
    ],
)

server.shell(
    name="Create default WireGuard peer via API",
    commands=[
        f"""
        PEER_STAMP=/etc/wg-portal/.pyinfra-peer-stamp
        if [ -f "$PEER_STAMP" ]; then exit 0; fi

        BASE_URL="http://{WGPORTAL["host"]}:{WGPORTAL["port"]}"
        AUTH="{creds["username"]}:{creds["api_token"]}"

        for i in $(seq 1 15); do
          STATUS=$(curl -s -o /dev/null -w '%{{http_code}}' \
            -u "$AUTH" "$BASE_URL/api/v1/peer/prepare/wg0" 2>/dev/null || true)
          if [ "$STATUS" = "200" ]; then break; fi
          sleep 2
        done

        PEER=$(curl -sf -u "$AUTH" "$BASE_URL/api/v1/peer/prepare/wg0" 2>/dev/null || true)
        if [ -z "$PEER" ]; then echo "wg-portal: failed to prepare peer" >&2; exit 1; fi

        PEER=$(echo "$PEER" | python3 -c "
import json, sys
d = json.load(sys.stdin)
d['DisplayName'] = 'Default'
d['Mode'] = 'client'
d['AllowedIPs'] = {{'Value': ['0.0.0.0/0', '::/0'], 'Overridable': True}}
print(json.dumps(d))
")

        HTTP_CODE=$(curl -s -o /dev/null -w '%{{http_code}}' \
          -X POST "$BASE_URL/api/v1/peer/new" \
          -H "Content-Type: application/json" \
          -u "$AUTH" \
          -d "$PEER" 2>/dev/null)

        if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "201" ]; then
          touch "$PEER_STAMP"
        elif [ "$HTTP_CODE" = "409" ]; then
          touch "$PEER_STAMP"
        else
          echo "wg-portal: peer creation failed with HTTP $HTTP_CODE" >&2
          exit 1
        fi
        """,
    ],
)
