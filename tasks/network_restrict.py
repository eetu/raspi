"""Network restrictions: nftables cgroup-based egress filtering for LAN-only services.

Uses nftables `socket cgroupv2` matching to block and log outbound traffic
from restricted services to destinations outside localhost and LAN.
Blocked attempts are logged with a "BREACH:<service>:" prefix in the kernel
journal, which the network monitor timer picks up for ntfy alerts.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, WIREGUARD

LAN_CIDR = NETWORK["lan_cidr"]
WG_SUBNET = WIREGUARD["subnet"]

# Services restricted to LAN-only access, mapped to their systemd unit names.
# VuIO additionally needs SSDP multicast (239.255.255.250).
# Syncthing additionally needs local discovery on udp/21027:
#   IPv4 limited broadcast (255.255.255.255) and IPv6 multicast (ff12::8384).
RESTRICTED = [
    "audiobookshelf",
    "beszel-hub",
    "beszel-agent",
    "navidrome",
    "ntfy",
    "syncthing",
    "wg-portal",
    "vuio",
]

_drop_rules = "\n".join(
    f'        socket cgroupv2 level 2 "system.slice/{svc}.service"'
    f' log prefix "BREACH:{svc}: " counter drop'
    for svc in RESTRICTED
)

nft_rules = f"""\
table inet service_restrict {{
    chain output {{
        type filter hook output priority 10 ; policy accept ;

        # Allow localhost, LAN, and WireGuard subnet for all services
        ip daddr {{ 127.0.0.0/8, {LAN_CIDR}, {WG_SUBNET} }} accept
        ip6 daddr {{ ::1, fe80::/10 }} accept

        # Allow SSDP multicast (DLNA discovery)
        ip daddr 239.255.255.250 accept

        # Allow Syncthing local discovery (link-local, never leaves LAN)
        ip daddr 255.255.255.255 udp dport 21027 accept
        ip6 daddr ff12::8384 udp dport 21027 accept

        # Block and log restricted services reaching outside LAN
{_drop_rules}
    }}
}}
"""

_boot_service = """\
[Unit]
Description=Load nftables service egress restrictions
After=network-pre.target
Before=network-online.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/modprobe nft_socket
ExecStart=/usr/sbin/nft -f /etc/nftables.d/service-restrict.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

_rules_hash = hashlib.sha256((nft_rules + _boot_service).encode()).hexdigest()

files.directory(
    name="Create /etc/nftables.d",
    path="/etc/nftables.d",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write nftables service restriction rules",
    src=io.BytesIO(nft_rules.encode()),
    dest="/etc/nftables.d/service-restrict.nft",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write nft-service-restrict boot service",
    src=io.BytesIO(_boot_service.encode()),
    dest="/etc/systemd/system/nft-service-restrict.service",
    user="root",
    group="root",
    mode="644",
)

# Ensure nft_socket kernel module loads at boot
files.put(
    name="Load nft_socket module at boot",
    src=io.BytesIO(b"nft_socket\n"),
    dest="/etc/modules-load.d/nft-socket.conf",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable nft-service-restrict",
    service="nft-service-restrict",
    enabled=True,
    daemon_reload=True,
)

server.shell(
    name="Apply nftables service restrictions",
    commands=[
        f"""
        STAMP=/etc/nftables.d/.service-restrict-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_rules_hash}" ]; then
          modprobe nft_socket 2>/dev/null || true
          nft delete table inet service_restrict 2>/dev/null || true
          nft -f /etc/nftables.d/service-restrict.nft
          echo '{_rules_hash}' > "$STAMP"
        fi
        """,
    ],
)
