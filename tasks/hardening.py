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
    name="UFW: configure rules",
    commands=[
        f"""
        ufw --force reset
        ufw default deny incoming
        ufw default allow outgoing
        ufw default deny routed
        ufw allow from {NETWORK["lan_cidr"]} to any port 22 proto tcp comment 'SSH LAN'
        ufw allow from {WIREGUARD["subnet"]} to any port 22 proto tcp comment 'SSH WG'
        ufw allow from {NETWORK["lan_cidr"]} to any port 53 comment 'DNS LAN'
        ufw allow from {WIREGUARD["subnet"]} to any port 53 comment 'DNS WG'
        ufw allow from {NETWORK["lan_cidr"]} to any port 80 proto tcp comment 'HTTP LAN'
        ufw allow from {NETWORK["lan_cidr"]} to any port 443 proto tcp comment 'HTTPS LAN'
        ufw allow from {WIREGUARD["subnet"]} to any port 443 proto tcp comment 'HTTPS WG'
        ufw allow {WIREGUARD["port"]}/udp comment 'WireGuard'
        ufw --force enable
        """,
    ],
)

systemd.service(name="Enable ufw", service="ufw", enabled=True, running=True)
