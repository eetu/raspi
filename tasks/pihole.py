"""Pi-hole v6: unattended install, web port, blocklists, password."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import NETWORK, PIHOLE

# --- setupVars for unattended installer ---

setup_vars = f"""\
PIHOLE_INTERFACE=eth0
PIHOLE_DNS_1={PIHOLE["dns1"]}
PIHOLE_DNS_2={PIHOLE["dns2"]}
QUERY_LOGGING=true
INSTALL_WEB_SERVER=true
INSTALL_WEB_INTERFACE=true
LIGHTTPD_ENABLED=false
"""

files.directory(
    name="Create /etc/pihole",
    path="/etc/pihole",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write Pi-hole setupVars.conf",
    src=io.BytesIO(setup_vars.encode()),
    dest="/etc/pihole/setupVars.conf",
    user="root",
    group="root",
    mode="644",
)

# --- Install (skipped if already installed) ---

server.shell(
    name="Install Pi-hole (unattended)",
    commands=[
        """
        if ! command -v pihole >/dev/null 2>&1; then
          curl -sSL https://install.pi-hole.net | bash /dev/stdin --unattended
        fi
        """,
    ],
)

# --- Web port: bind to localhost:8080 ---

server.shell(
    name="Set Pi-hole web port to 127.0.0.1:8080",
    commands=[
        f"""
        WANT="127.0.0.1:{PIHOLE["web_port"]}o"
        CURRENT=$(pihole-FTL --config webserver.port 2>/dev/null || true)
        if [ "$CURRENT" != "$WANT" ]; then
          pihole-FTL --config webserver.port "$WANT"
        fi
        """,
    ],
)

# --- Admin password ---

_pw_hash = hashlib.sha256(bw.pihole_password().encode()).hexdigest()

server.shell(
    name="Set Pi-hole admin password",
    commands=[
        f"""
        STAMP=/etc/pihole/.pw-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_pw_hash}" ]; then
          pihole setpassword '{bw.pihole_password()}'
          echo '{_pw_hash}' > "$STAMP"
        fi
        """,
    ],
)

# --- Blocklists + gravity (INSERT OR IGNORE is idempotent; gravity guarded by stamp) ---

_blocklist_hash = hashlib.sha256("".join(PIHOLE["blocklists"]).encode()).hexdigest()

for url in PIHOLE["blocklists"]:
    server.shell(
        name=f"Add blocklist: {url.split('/')[-1]}",
        commands=[
            f"""sqlite3 /etc/pihole/gravity.db \
"INSERT OR IGNORE INTO adlist (address, enabled, comment) \
VALUES ('{url}', 1, 'hagezi')" """,
        ],
    )

server.shell(
    name="Update Pi-hole gravity",
    commands=[
        f"""
        STAMP=/etc/pihole/.gravity-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_blocklist_hash}" ]; then
          pihole -g
          echo '{_blocklist_hash}' > "$STAMP"
        fi
        """,
    ],
)

# --- Listen on all interfaces (needed for WireGuard DNS) ---

server.shell(
    name="Set Pi-hole to listen on all interfaces",
    commands=[
        """
        WANT="local"
        CURRENT=$(pihole-FTL --config dns.listeningMode 2>/dev/null || true)
        if [ "$CURRENT" != "$WANT" ]; then
          pihole-FTL --config dns.listeningMode "$WANT"
          systemctl restart pihole-FTL
        fi
        """,
    ],
)

# --- Upstream DNS (Quad9 unfiltered, no DNSSEC) ---

server.shell(
    name="Set upstream DNS servers",
    commands=[
        f"""
        WANT='["{PIHOLE["dns1"]}", "{PIHOLE["dns2"]}"]'
        CURRENT=$(pihole-FTL --config dns.upstreams 2>/dev/null || true)
        if [ "$CURRENT" != "$WANT" ]; then
          pihole-FTL --config dns.upstreams "$WANT"
        fi
        """,
    ],
)

systemd.service(
    name="Enable pihole-FTL",
    service="pihole-FTL",
    enabled=True,
    running=True,
)

# --- Local DNS: resolve internal subdomains to WireGuard IP (split DNS for VPN clients) ---

_subdomains = ["hcc", "pihole", "abs", "vpn", "ntfy", "status"]
_local_dns = (
    "\n".join(f"{NETWORK['lan_ip']} {sub}.{NETWORK['domain']}" for sub in _subdomains) + "\n"
)

files.put(
    name="Write Pi-hole local DNS records",
    src=io.BytesIO(_local_dns.encode()),
    dest="/etc/pihole/custom.list",
    user="root",
    group="root",
    mode="644",
)
