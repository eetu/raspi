"""Pi-hole v6: unattended install, web port, blocklists, password."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import KANIDM_OIDC_CLIENTS, NETWORK, PIHOLE, UNBOUND

# --- setupVars for unattended installer ---

_unbound_upstream = f"127.0.0.1#{UNBOUND['port']}"

setup_vars = f"""\
PIHOLE_INTERFACE=eth0
PIHOLE_DNS_1={_unbound_upstream}
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

_pihole_installer_url = (
    f"https://raw.githubusercontent.com/pi-hole/pi-hole/{PIHOLE['version']}"
    "/automated%20install/basic-install.sh"
)

server.shell(
    name=f"Install Pi-hole {PIHOLE['version']} (unattended)",
    commands=[
        f"""
        if ! command -v pihole >/dev/null 2>&1; then
          INSTALLER=$(mktemp)
          curl -fsSL "{_pihole_installer_url}" -o "$INSTALLER"
          echo '{PIHOLE["installer_sha256"]}  '"$INSTALLER" | sha256sum -c - || {{
            echo "Pi-hole installer SHA-256 mismatch — aborting" >&2
            rm -f "$INSTALLER"
            exit 1
          }}
          bash "$INSTALLER" --unattended
          rm -f "$INSTALLER"
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
# Disable Pi-hole's own auth when oauth2-proxy is active — Traefik enforces
# SSO before requests reach Pi-hole, and the web UI is localhost-only anyway.

_op_oidc = KANIDM_OIDC_CLIENTS.get("oauth2-proxy")
_op_secret = bw.kanidm_oidc_secret(_op_oidc["secret_field"]) if _op_oidc else ""

server.shell(
    name="Set Pi-hole admin password",
    commands=[
        "pihole setpassword ''" if _op_secret else f"pihole setpassword '{bw.pihole_password()}'"
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

# --- Upstream DNS (Quad9 unfiltered, no DNSSEC — IPv4 + IPv6) ---

server.shell(
    name="Set upstream DNS to Unbound",
    commands=[
        f"""
        WANT='["{_unbound_upstream}"]'
        CURRENT=$(pihole-FTL --config dns.upstreams 2>/dev/null || true)
        if [ "$CURRENT" != "$WANT" ]; then
          pihole-FTL --config dns.upstreams "$WANT"
        fi
        """,
    ],
)

files.directory(
    name="Create pihole-FTL.service.d drop-in dir",
    path="/etc/systemd/system/pihole-FTL.service.d",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Cap pihole-FTL memory at 128M",
    src=io.BytesIO(b"[Service]\nMemoryMax=128M\n"),
    dest="/etc/systemd/system/pihole-FTL.service.d/memory.conf",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable pihole-FTL",
    service="pihole-FTL",
    enabled=True,
    running=True,
    daemon_reload=True,
)

# --- Reduce SD writes: flush query DB every 60 min instead of default 1 min ---

server.shell(
    name="Set Pi-hole FTL database write interval (60 min)",
    commands=[
        """
        WANT="60"
        CURRENT=$(pihole-FTL --config database.DBinterval 2>/dev/null | tr -d '[:space:]' || true)
        if [ "$CURRENT" != "$WANT" ]; then
          pihole-FTL --config database.DBinterval 60
        fi
        """,
    ],
)

# --- Limit query history to avoid unbounded DB growth (default is 365 days) ---

server.shell(
    name=f"Set Pi-hole query history retention ({PIHOLE['history_days']} days)",
    commands=[
        f"""
        WANT="{PIHOLE["history_days"]}"
        CURRENT=$(pihole-FTL --config database.maxDBdays 2>/dev/null | tr -d '[:space:]' || true)
        if [ "$CURRENT" != "$WANT" ]; then
          pihole-FTL --config database.maxDBdays "$WANT"
        fi
        """,
    ],
)

# --- Local DNS: resolve internal subdomains to WireGuard IP (split DNS for VPN clients) ---

_subdomains = ["hcc", "pihole", "abs", "vpn", "ntfy", "status", "auth"]
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
