import hashlib

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK, WIREGUARD

# --- traefik system user (created early so secrets_files.py can assign group ownership) ---

server.group(name="Create traefik group", group="traefik", system=True)
server.user(
    name="Create traefik user",
    user="traefik",
    group="traefik",
    system=True,
    shell="/usr/sbin/nologin",
    home="/nonexistent",
)

with open("files/sshd_config", "rb") as _f:
    _sshd_hash = hashlib.sha256(_f.read()).hexdigest()

with open("files/fail2ban-jail.local", "rb") as _f:
    _fail2ban_hash = hashlib.sha256(_f.read()).hexdigest()

with open("files/journald.conf", "rb") as _f:
    _journald_hash = hashlib.sha256(_f.read()).hexdigest()

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

# --- SD card write reduction ---

# Journal: keep logs in RAM only (lost on reboot, but /run is already tmpfs)
files.directory(
    name="Create /etc/systemd/journald.conf.d",
    path="/etc/systemd/journald.conf.d",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Configure journald (volatile, 64M cap)",
    src="files/journald.conf",
    dest="/etc/systemd/journald.conf.d/99-raspi.conf",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Restart journald if config changed",
    commands=[
        f"""
        STAMP=/etc/systemd/journald.conf.d/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_journald_hash}" ]; then
          systemctl restart systemd-journald
          echo '{_journald_hash}' > "$STAMP"
        fi
        """,
    ],
)

# fstab: noatime on root to avoid inode access-time writes
server.shell(
    name="fstab: add noatime to root ext4 mount",
    commands=[
        r"""
        if ! grep -E '^\S+\s+/\s+ext4.*noatime' /etc/fstab >/dev/null 2>&1; then
          sed -i -E 's|^(\S+)(\s+/\s+ext4\s+)(defaults)|\1\2\3,noatime|' /etc/fstab
        fi
        """,
    ],
)

# fstab: /tmp on tmpfs (keeps SD writes out of /tmp entirely)
server.shell(
    name="fstab: mount /tmp as tmpfs",
    commands=[
        """
        grep -q 'tmpfs /tmp tmpfs' /etc/fstab || \
          echo 'tmpfs /tmp tmpfs defaults,noatime,nosuid,mode=1777,size=32m 0 0' >> /etc/fstab
        mountpoint -q /tmp || mount /tmp 2>/dev/null || true
        """,
    ],
)

# zram: compressed swap in RAM — replaces SD swapfile, provides OOM safety net
# 25% of 1 GB = 256 MB zram device → ~512 MB effective swap at lz4 2:1 compression
files.put(
    name="Configure zram-generator",
    src="files/zram-generator.conf",
    dest="/etc/systemd/zram-generator.conf",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Activate zram swap",
    commands=[
        """
        if ! swapon --show | grep -q zram; then
          systemctl daemon-reload
          systemctl start systemd-zram-setup@zram0.service 2>/dev/null || true
        fi
        """,
    ],
)

# Disable dphys-swapfile (Raspberry Pi OS default: 100 MB swapfile on SD card)
server.shell(
    name="Disable dphys-swapfile",
    commands=[
        """
        if systemctl is-enabled dphys-swapfile >/dev/null 2>&1; then
          dphys-swapfile swapoff 2>/dev/null || true
          systemctl disable dphys-swapfile
          systemctl stop dphys-swapfile 2>/dev/null || true
        fi
        """,
    ],
)
