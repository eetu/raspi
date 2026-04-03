"""WireGuard: keypair generation, wg0.conf, IP forwarding, systemd."""

import base64
import io

from pyinfra import logger
from pyinfra.operations import files, server, systemd

import secrets as bw
from group_data.all import WIREGUARD


def _generate_keypair():
    """Generate WireGuard keypair using Python cryptography (no wg binary needed)."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    private_key = X25519PrivateKey.generate()
    private_b64 = base64.b64encode(private_key.private_bytes_raw()).decode()
    public_b64 = base64.b64encode(private_key.public_key().public_bytes_raw()).decode()
    return private_b64, public_b64


# --- Keypair: fetch from Bitwarden or generate ---

existing = bw.wg_server_key()
if existing.get("private_key"):
    private_key = existing["private_key"]
    public_key = existing["public_key"]
    logger.info("WireGuard: using existing keypair from Bitwarden")
else:
    private_key, public_key = _generate_keypair()
    bw.save_wg_server_key(private_key, public_key)
    logger.info(f"WireGuard: generated new keypair, public key: {public_key}")

# --- wg0.conf ---

wg0_conf = f"""[Interface]
Address    = {WIREGUARD["ip"]}/24
ListenPort = {WIREGUARD["port"]}
PrivateKey = {private_key}
# NAT: masquerade VPN traffic through the default outbound interface
PostUp   = iptables -A FORWARD -i %i -j ACCEPT; iptables -A FORWARD -o %i -j ACCEPT; iptables -t nat -A POSTROUTING -o $(ip route show default | awk '{{print $5; exit}}') -j MASQUERADE
PostDown = iptables -D FORWARD -i %i -j ACCEPT; iptables -D FORWARD -o %i -j ACCEPT; iptables -t nat -D POSTROUTING -o $(ip route show default | awk '{{print $5; exit}}') -j MASQUERADE
# Peers are managed by wg-portal — do not edit below this line
"""

files.directory(
    name="Create /etc/wireguard",
    path="/etc/wireguard",
    user="root",
    group="root",
    mode="700",
    present=True,
)

files.put(
    name="Write wg0.conf",
    src=io.BytesIO(wg0_conf.encode()),
    dest="/etc/wireguard/wg0.conf",
    user="root",
    group="root",
    mode="600",
)

# --- IP forwarding ---

files.put(
    name="Enable IP forwarding",
    src=io.BytesIO(b"net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=1\n"),
    dest="/etc/sysctl.d/99-wireguard.conf",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Apply sysctl forwarding",
    commands=["sysctl -p /etc/sysctl.d/99-wireguard.conf"],
)

# --- systemd ---

systemd.service(
    name="Enable wg-quick@wg0",
    service="wg-quick@wg0",
    enabled=True,
    running=True,
    daemon_reload=True,
)
