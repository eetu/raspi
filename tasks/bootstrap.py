from pyinfra.operations import apt, files

apt.update(name="Update apt cache")

apt.upgrade(
    name="Upgrade all packages",
    auto_remove=True,
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
