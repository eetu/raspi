"""Pi-hole v6: unattended install, web port, blocklists, password."""

import io

from pyinfra.operations import files, server, systemd

import secrets as bw
from group_data.all import PIHOLE

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

# --- Install ---

server.shell(
    name="Install Pi-hole (unattended)",
    commands=[
        "curl -sSL https://install.pi-hole.net | bash /dev/stdin --unattended",
    ],
    timeout=300,
)

# --- Web port: bind to localhost:8080 ---

server.shell(
    name="Set Pi-hole web port to 127.0.0.1:8080",
    commands=[
        f"pihole-FTL --config webserver.port '127.0.0.1:{PIHOLE['web_port']}o'",
    ],
)

# --- Admin password ---

server.shell(
    name="Set Pi-hole admin password",
    commands=[
        f"pihole setpassword '{bw.pihole_password()}'",
    ],
)

# --- Blocklists ---

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
    commands=["pihole -g"],
    timeout=300,
)

# --- Upstream DNS (Quad9 unfiltered, no DNSSEC) ---

server.shell(
    name="Set upstream DNS servers",
    commands=[
        'pihole-FTL --config dns.upstreams \'["9.9.9.10", "149.112.112.10"]\'',
    ],
)

systemd.service(
    name="Restart pihole-FTL",
    service="pihole-FTL",
    enabled=True,
    restarted=True,
)
