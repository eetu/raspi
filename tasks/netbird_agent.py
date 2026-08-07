"""NetBird agent on the Pi — the routing peer that carries LAN traffic.

This is what actually replaces wg0. Mesh clients reach the LAN (and every vhost
behind Traefik) through this peer via the `home-lan` network route declared in
NETBIRD["routes"], and it is the nameserver target that puts Pi-hole's split DNS
on the mesh. The coordinator alone would give you a control plane and no path in.

Installed from the pinned `.deb` on the GitHub release rather than via
`pkgs.netbird.io/install.sh`. The install script adds netbird's apt repository,
which would let unattended-upgrades move the client out from under a pinned
server — the release deb has no dependencies (it ships `/usr/bin/netbird` and a
postinst that runs `netbird service install`), so there is nothing to gain from
the repo and a version-drift risk in keeping it.

Enrolment waits for the setup key, which is the second half of the bootstrap:
deploy 1 has tasks/netbird_reconcile.py create the key and store it in the vault,
deploy 2 has tasks/secrets.py write it to /etc/secrets/netbird-setup-key and this
task enrol with it. Nothing here fails if the key is not there yet.
"""

import io

from pyinfra.operations import files, server, systemd

from group_data.all import NETWORK
from tasks.util import optional

NETBIRD = optional("NETBIRD")

_SETUP_KEY_FILE = "/etc/secrets/netbird-setup-key"

# Rendered into the `netbird up` invocation only when a port is declared, so an
# unset NETBIRD["agent_wireguard_port"] leaves the client on its own default and
# keeps the firewall closed (relay-only).
_wg_port = (NETBIRD or {}).get("agent_wireguard_port")
_WG_PORT_FLAG = f"\n              --wireguard-port {_wg_port} \\" if _wg_port else ""


if NETBIRD is not None:
    VERSION = NETBIRD["agent_version"]
    MGMT_URL = f"https://{NETBIRD['url_prefix']}.{NETWORK['domain']}"
    DEB_URL = (
        f"https://github.com/netbirdio/netbird/releases/download/v{VERSION}/"
        f"netbird_{VERSION}_linux_arm64.deb"
    )

    # --- Forwarding ---
    #
    # A routing peer has to forward for other peers. The netbird client does set
    # up its own firewall rules, but the sysctl is host policy — declare it rather
    # than depend on the client's side effects, and clean up the file wg-quick
    # used to own so there is one obvious source of truth.
    files.put(
        name="Enable IP forwarding for the NetBird routing peer",
        src=io.BytesIO(b"net.ipv4.ip_forward=1\nnet.ipv6.conf.all.forwarding=1\n"),
        dest="/etc/sysctl.d/99-netbird.conf",
        user="root",
        group="root",
        mode="644",
    )

    files.file(
        name="Remove the WireGuard forwarding sysctl",
        path="/etc/sysctl.d/99-wireguard.conf",
        present=False,
    )

    server.shell(
        name="Apply sysctl forwarding",
        commands=["sysctl -p /etc/sysctl.d/99-netbird.conf"],
    )

    # --- Client ---
    #
    # dpkg -i covers install, upgrade and downgrade, so a version bump either way
    # is just a config change. The postinst reinstalls the systemd unit and starts
    # it; the enrolment below is a no-op once the peer is connected.
    server.shell(
        name=f"Install NetBird agent {VERSION}",
        commands=[
            f"""
            INSTALLED=$(netbird version 2>/dev/null | grep -o "{VERSION}" || true)
            if [ "$INSTALLED" != "{VERSION}" ]; then
              TMP=$(mktemp -d)
              trap 'rm -rf "$TMP"' EXIT
              curl -fsSL "{DEB_URL}" -o "$TMP/netbird.deb"
              dpkg -i "$TMP/netbird.deb"
            fi
            """,
        ],
    )

    # The deb's postinst runs `netbird service install` + start, but `netbird up`
    # below talks to that daemon over its socket — so make sure it is actually
    # running before enrolling, not after.
    systemd.service(
        name="Ensure the NetBird agent is enabled and running",
        service="netbird",
        enabled=True,
        running=True,
    )

    # --- Enrolment ---
    #
    # Run unconditionally rather than guarding on a state file. `netbird up` is
    # idempotent — when the peer is already connected it prints "Already connected"
    # and exits 0 without touching the setup key (client/cmd/up.go) — and running
    # it every time makes the peer self-healing: if it is ever deleted server-side,
    # the next deploy re-enrols it instead of silently staying offline. Guarding on
    # a state file would be worse than useless — the daemon creates its own profile
    # under /var/lib/netbird on first start, well before any peer exists.
    #
    # `--setup-key-file` keeps the key out of the process table. `--disable-dns` is
    # load-bearing: the client otherwise takes over the host's resolver config, and
    # this host *is* the resolver (Pi-hole on :53, Unbound on :5335). The Pi is a
    # routing peer, not a consumer of mesh DNS names.
    #
    # `--wireguard-port` is pinned rather than left at the client default so it
    # cannot drift away from the ufw rule and the router's forward — the three have
    # to agree or peers silently fall back to the relay.
    #
    # CAVEAT: because `up` short-circuits on "Already connected", flags changed here
    # are NOT applied to a peer that is already up — they only take effect on a fresh
    # enrolment. Changing the port therefore needs `netbird down` first (or clearing
    # /var/lib/netbird). Harmless while the pin equals the client default, which is
    # why this is a comment and not a rebuild-on-change dance.
    server.shell(
        name="Enrol the NetBird agent",
        commands=[
            f"""
            if [ ! -s {_SETUP_KEY_FILE} ]; then
              echo "netbird: no setup key at {_SETUP_KEY_FILE} yet — enrolment lands on the next deploy" >&2
              exit 0
            fi
            netbird up \\
              --setup-key-file {_SETUP_KEY_FILE} \\
              --management-url '{MGMT_URL}' \\
              --hostname '{NETBIRD["agent_hostname"]}' \\{_WG_PORT_FLAG}
              --disable-dns
            """,
        ],
    )
