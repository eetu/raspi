from pyinfra.operations import apt, files, server

UPGRADE_STAMP = "/var/lib/apt/raspi-last-upgrade"
UPGRADE_MAX_AGE_HOURS = 24

apt.update(name="Update apt cache")

server.shell(
    name="Upgrade all packages (once per 24h)",
    commands=[
        f"""
        STAMP="{UPGRADE_STAMP}"
        MAX_AGE={UPGRADE_MAX_AGE_HOURS * 3600}
        if [ ! -f "$STAMP" ] || [ $(( $(date +%s) - $(stat -c %Y "$STAMP") )) -gt $MAX_AGE ]; then
          apt-get upgrade -y --autoremove
          touch "$STAMP"
        fi
        """,
    ],
)

apt.packages(
    name="Install base packages",
    packages=[
        "curl",
        "wget",
        "ca-certificates",
        "gnupg",
        "vim",
        "htop",
        "sqlite3",
        "cifs-utils",
        "wireguard-tools",
        "fail2ban",
        "unattended-upgrades",
        "apt-listchanges",
        "ufw",
        "podman",
        "iptables",
    ],
    update=True,
)

files.directory(
    name="Create /etc/secrets (700)",
    path="/etc/secrets",
    user="root",
    group="root",
    mode="700",
    present=True,
)
