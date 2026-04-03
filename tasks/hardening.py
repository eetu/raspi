import hashlib

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, WIREGUARD

with open("files/sshd_config", "rb") as _f:
    _sshd_hash = hashlib.sha256(_f.read()).hexdigest()

with open("files/fail2ban-jail.local", "rb") as _f:
    _fail2ban_hash = hashlib.sha256(_f.read()).hexdigest()

# --- sshd ---

files.put(
    name="Configure sshd",
    src="files/sshd_config",
    dest="/etc/ssh/sshd_config",
    user="root",
    group="root",
    mode="644",
)

systemd.service(name="Enable ssh", service="ssh", enabled=True, running=True)

server.shell(
    name="Restart ssh if config changed",
    commands=[
        f"""
        STAMP=/etc/ssh/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_sshd_hash}" ]; then
          systemctl restart ssh
          echo '{_sshd_hash}' > "$STAMP"
        fi
        """,
    ],
)

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

server.shell(
    name="Restart fail2ban if config changed",
    commands=[
        f"""
        STAMP=/etc/fail2ban/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_fail2ban_hash}" ]; then
          systemctl restart fail2ban
          echo '{_fail2ban_hash}' > "$STAMP"
        fi
        """,
    ],
)

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

_ufw_rules = (
    f"from {NETWORK['lan_cidr']} 22tcp "
    f"from {WIREGUARD['subnet']} 22tcp "
    f"from {NETWORK['lan_cidr']} 53 "
    f"from {WIREGUARD['subnet']} 53 "
    f"from {NETWORK['lan_cidr']} 80tcp "
    f"from {NETWORK['lan_cidr']} 443tcp "
    f"from {WIREGUARD['subnet']} 443tcp "
    f"{WIREGUARD['port']}udp "
    f"route wg0"
)

server.shell(
    name="UFW: configure rules",
    commands=[
        f"""
        STAMP=/etc/ufw/.pyinfra-stamp
        WANT="{_ufw_rules}"
        CURRENT=$(cat "$STAMP" 2>/dev/null || true)
        if [ "$CURRENT" = "$WANT" ]; then exit 0; fi
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
        ufw route allow in on wg0 comment 'WireGuard forwarding'
        ufw --force enable
        echo "$WANT" > "$STAMP"
        """,
    ],
)

systemd.service(name="Enable ufw", service="ufw", enabled=True, running=True)
