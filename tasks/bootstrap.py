from pyinfra.operations import apt, files, server, systemd

from group_data.all import HOSTS

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
        "winbind",
        # avahi + libnss-mdns let `getent hosts foo.local` resolve mDNS names,
        # which is how the CIFS mounts (and anything else) find the NAS without
        # a hard-coded IP that DHCP loves to renegotiate.
        "avahi-daemon",
        "libnss-mdns",
        "wireguard-tools",
        "fail2ban",
        "unattended-upgrades",
        "apt-listchanges",
        "ufw",
        "podman",
        "iptables",
        "systemd-zram-generator",
    ],
    update=True,
)

server.shell(
    name="Enable wins + mdns_minimal in nsswitch.conf",
    commands=[
        """
        if ! grep -q 'wins' /etc/nsswitch.conf; then
          sed -i 's/^hosts:.*/& wins/' /etc/nsswitch.conf
        fi
        if ! grep -q 'mdns_minimal' /etc/nsswitch.conf; then
          # Insert mdns_minimal [NOTFOUND=return] before `dns` so .local lookups
          # take the avahi path; libnss-mdns Debian default ordering.
          sed -i -E 's/^(hosts:.*)\\bdns\\b/\\1mdns_minimal [NOTFOUND=return] dns/' /etc/nsswitch.conf
        fi
        """,
    ],
)

systemd.service(
    name="Enable avahi-daemon",
    service="avahi-daemon",
    enabled=True,
    running=True,
)

server.shell(
    name="Enable memory cgroup controller (requires reboot if changed)",
    commands=[
        """
        CMDLINE=/boot/firmware/cmdline.txt
        if ! grep -q 'cgroup_memory=1' "$CMDLINE"; then
          sed -i 's/$/ cgroup_memory=1 cgroup_enable=memory/' "$CMDLINE"
          echo "REBOOT_REQUIRED: memory cgroup enabled"
        fi
        """,
    ],
)

files.directory(
    name="Create /etc/secrets (700)",
    path="/etc/secrets",
    user="root",
    group="root",
    mode="700",
    present=True,
)

for _hostname, _target in HOSTS.items():
    # mDNS-valued entries are owned by tasks/host_discover.py (resolved via
    # avahi at boot + on a 5-minute timer). Bootstrap only writes literal IPs.
    if _target.endswith(".local"):
        continue
    server.shell(
        name=f"Set /etc/hosts entry for {_hostname}",
        commands=[
            f"""
            ENTRY="{_target} {_hostname}"
            if grep -q "\\b{_hostname}\\b" /etc/hosts; then
              sed -i "s/.*\\b{_hostname}\\b.*/$ENTRY/" /etc/hosts
            else
              echo "$ENTRY" >> /etc/hosts
            fi
            """,
        ],
    )
