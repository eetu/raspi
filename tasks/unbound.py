"""Unbound: recursive DNS resolver on 127.0.0.1:5335 (upstream for Pi-hole)."""

import io

from pyinfra.operations import apt, files, server, systemd

from group_data.all import UNBOUND

apt.packages(
    name="Install unbound",
    packages=["unbound"],
    update=False,
)

# Unbound ships a root hints file, but keeping it fresh is best practice.
server.shell(
    name="Download root hints",
    commands=[
        """
        if [ ! -f /var/lib/unbound/root.hints ] || \
           [ $(( $(date +%s) - $(stat -c %Y /var/lib/unbound/root.hints 2>/dev/null || echo 0) )) -gt 2592000 ]; then
          curl -fsSL https://www.internic.net/domain/named.root -o /var/lib/unbound/root.hints
        fi
        """,
    ],
)

_config = f"""\
server:
    # Listen only on loopback — Pi-hole is the only client
    interface: 127.0.0.1
    port: {UNBOUND["port"]}
    do-ip4: yes
    do-ip6: no
    do-udp: yes
    do-tcp: yes

    # Access control
    access-control: 127.0.0.0/8 allow

    # Root hints
    root-hints: /var/lib/unbound/root.hints

    # DNSSEC is enabled via the package default drop-in
    # (root-auto-trust-anchor-file.conf) — don't redeclare auto-trust-anchor-file here

    # Privacy: don't pass client subnet to upstream
    hide-identity: yes
    hide-version: yes

    # Cache — modest sizes appropriate for a Pi
    msg-cache-size: {UNBOUND["msg_cache_mb"]}m
    rrset-cache-size: {UNBOUND["rrset_cache_mb"]}m
    cache-min-ttl: 300
    cache-max-ttl: 86400

    # Performance: single thread is enough for home use
    num-threads: 1
    so-rcvbuf: 1m

    # Prefetch popular records before TTL expires
    prefetch: yes
    prefetch-key: yes

    # Harden against common attacks
    harden-glue: yes
    harden-dnssec-stripped: yes
    harden-below-nxdomain: yes
    use-caps-for-id: yes
    val-clean-additional: yes

    # Reduce SD writes: keep logs to a minimum
    verbosity: 0
    log-queries: no
    log-replies: no

    # Special-use zones that must never be recursed (RFC 6762, RFC 6303).
    # Matter/Thread devices query .service.arpa and .local for DNS-SD; returning
    # NXDOMAIN immediately (instead of SERVFAIL from a failed recursion) prevents
    # Thread border routers from retrying aggressively and losing track of devices.
    local-zone: "service.arpa." static
    local-zone: "local." static
"""

_config_path = "/etc/unbound/unbound.conf.d/pihole.conf"

files.put(
    name="Write unbound pihole.conf",
    src=io.BytesIO(_config.encode()),
    dest=_config_path,
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Validate unbound config",
    commands=["unbound-checkconf"],
)

files.directory(
    name="Create unbound.service.d drop-in dir",
    path="/etc/systemd/system/unbound.service.d",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Cap unbound memory at 64M",
    src=io.BytesIO(b"[Service]\nMemoryMax=64M\n"),
    dest="/etc/systemd/system/unbound.service.d/memory.conf",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable and start unbound",
    service="unbound",
    enabled=True,
    running=True,
    restarted=False,
    daemon_reload=True,
)

server.shell(
    name="Restart unbound if config changed",
    commands=[
        f"""
        STAMP=/etc/unbound/.config-stamp
        HASH=$(sha256sum {_config_path} | cut -d' ' -f1)
        if [ "$(cat "$STAMP" 2>/dev/null)" != "$HASH" ]; then
          systemctl restart unbound
          echo "$HASH" > "$STAMP"
        fi
        """,
    ],
)
