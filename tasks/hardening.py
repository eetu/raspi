from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, WIREGUARD

# --- sshd ---

files.put(
    name="Configure sshd",
    src="files/sshd_config",
    dest="/etc/ssh/sshd_config",
    user="root",
    group="root",
    mode="644",
)

systemd.service(name="Restart ssh", service="ssh", restarted=True)

# --- fail2ban ---

files.put(
    name="Configure fail2ban jail",
    src="files/fail2ban-jail.local",
    dest="/etc/fail2ban/jail.local",
    user="root",
    group="root",
    mode="644",
)

systemd.service(name="Enable fail2ban", service="fail2ban", enabled=True, running=True)

# --- unattended-upgrades ---

files.put(
    name="Configure unattended-upgrades",
    src="files/20auto-upgrades",
    dest="/etc/apt/apt.conf.d/20auto-upgrades",
    user="root",
    group="root",
    mode="644",
)

# --- ufw ---

server.shell(
    name="UFW: reset and set defaults",
    commands=[
        "ufw --force reset",
        "ufw default deny incoming",
        "ufw default allow outgoing",
        "ufw default deny routed",
    ],
)

server.shell(
    name="UFW: SSH from LAN and WireGuard",
    commands=[
        f"ufw allow from {NETWORK['lan_cidr']} to any port 22 proto tcp comment 'SSH LAN'",
        f"ufw allow from {WIREGUARD['subnet']} to any port 22 proto tcp comment 'SSH WG'",
    ],
)

server.shell(
    name="UFW: Pi-hole DNS",
    commands=[
        f"ufw allow from {NETWORK['lan_cidr']} to any port 53 comment 'DNS LAN'",
        f"ufw allow from {WIREGUARD['subnet']} to any port 53 comment 'DNS WG'",
    ],
)

server.shell(
    name="UFW: Traefik HTTP/HTTPS",
    commands=[
        f"ufw allow from {NETWORK['lan_cidr']} to any port 80 proto tcp comment 'HTTP LAN'",
        f"ufw allow from {NETWORK['lan_cidr']} to any port 443 proto tcp comment 'HTTPS LAN'",
        f"ufw allow from {WIREGUARD['subnet']} to any port 443 proto tcp comment 'HTTPS WG'",
    ],
)

server.shell(
    name="UFW: WireGuard",
    commands=[
        f"ufw allow {WIREGUARD['port']}/udp comment 'WireGuard'",
    ],
)

server.shell(name="UFW: enable", commands=["ufw --force enable"])

systemd.service(name="Enable ufw", service="ufw", enabled=True, running=True)
