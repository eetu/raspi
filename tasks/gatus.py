"""Gatus: lightweight uptime monitoring + status page (Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS, GATUS, NETWORK, NTFY, UNBOUND
from tasks.util import resolve_latest

DOMAIN = NETWORK["domain"]

_image = (
    resolve_latest("TwiN/gatus", GATUS["image"]) if GATUS.get("resolve_latest") else GATUS["image"]
)

_config_yaml = f"""\
alerting:
  ntfy:
    url: "https://ntfy.{DOMAIN}"
    topic: "{NTFY["topic"]}"
    default-alert:
      enabled: true
      failure-threshold: 3
      success-threshold: 1
      send-on-resolved: true

endpoints:
  - name: HCC
    url: "https://hcc.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
    alerts:
      - type: ntfy

  - name: Pi-hole
    url: "https://pihole.{DOMAIN}/admin"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
    alerts:
      - type: ntfy

  - name: Audiobookshelf
    url: "https://audiobooks.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
    alerts:
      - type: ntfy

  - name: WireGuard Portal
    url: "https://vpn.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
    alerts:
      - type: ntfy

  - name: ntfy
    url: "https://ntfy.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
    alerts:
      - type: ntfy

  - name: Vaultwarden
    url: "https://vault.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
    alerts:
      - type: ntfy

  - name: Unbound DNS
    url: "127.0.0.1:{UNBOUND["port"]}"
    interval: 2m
    dns:
      query-name: "pi-hole.net"
      query-type: "A"
    conditions:
      - "len([BODY]) > 0"
    alerts:
      - type: ntfy

  - name: Pi-hole DNS
    url: "{NETWORK["lan_ip"]}:53"
    interval: 2m
    dns:
      query-name: "pi-hole.net"
      query-type: "A"
    conditions:
      - "len([BODY]) > 0"
    alerts:
      - type: ntfy

  - name: Pi
    url: "icmp://{NETWORK["lan_ip"]}"
    interval: 1m
    conditions:
      - "[CONNECTED] == true"
    alerts:
      - type: ntfy

  - name: NAS
    url: "icmp://{CIFS["audiobooks"]["share"].split("/")[2]}"
    interval: 1m
    conditions:
      - "[CONNECTED] == true"
    alerts:
      - type: ntfy

  - name: Internet
    url: "icmp://1.1.1.1"
    interval: 1m
    conditions:
      - "[CONNECTED] == true"
    alerts:
      - type: ntfy

storage:
  type: sqlite
  path: /data/gatus.db

web:
  address: "{GATUS["host"]}"
  port: {GATUS["port"]}
"""

quadlet = f"""\
[Unit]
Description=Gatus monitoring
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=gatus
Image={_image}
Network=host
Volume=/etc/gatus/config.yaml:/config/config.yaml:ro
Volume=/var/lib/gatus:/data
AddCapability=CAP_NET_RAW

[Service]
Restart=always
RestartSec=10
MemoryMax={GATUS["memory_max"]}

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256((quadlet + _config_yaml).encode()).hexdigest()

files.directory(
    name="Create /etc/gatus",
    path="/etc/gatus",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.directory(
    name="Create /var/lib/gatus",
    path="/var/lib/gatus",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write gatus config.yaml",
    src=io.BytesIO(_config_yaml.encode()),
    dest="/etc/gatus/config.yaml",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write gatus.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/gatus.container",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Reload quadlet units",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true",
    ],
)

systemd.service(
    name="Start gatus",
    service="gatus",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart gatus if config changed",
    commands=[
        f"""
        STAMP=/etc/gatus/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart gatus
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)
