"""Traefik: download binary, static + dynamic config, systemd service."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import (
    AUDIOBOOKSHELF,
    GATUS,
    HCC,
    NAVIDROME,
    NETWORK,
    NTFY,
    PIHOLE,
    SYNCTHING,
    TRAEFIK,
    VAULTWARDEN,
    WGPORTAL,
    YARR,
)

VERSION = TRAEFIK["version"]
BINARY_URL = (
    f"https://github.com/traefik/traefik/releases/download/{VERSION}/"
    f"traefik_{VERSION}_linux_arm64.tar.gz"
)
DOMAIN = NETWORK["domain"]

# --- Binary ---

server.shell(
    name=f"Install Traefik {VERSION}",
    commands=[
        f"""
        INSTALLED=$(/usr/local/bin/traefik version 2>/dev/null | awk '/Version:/ {{print $2}}' || true)
        if [ "$INSTALLED" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" | tar -xz -C /usr/local/bin traefik
          chmod +x /usr/local/bin/traefik
        fi
        """,
    ],
)

# --- Directories ---

for path in ("/etc/traefik", "/etc/traefik/dynamic"):
    files.directory(
        name=f"Create {path}",
        path=path,
        user="traefik",
        group="traefik",
        mode="750",
        present=True,
    )

# acme.json must exist with mode 600, owned by traefik, or Traefik refuses to start
server.shell(
    name="Create acme.json",
    commands=[
        """
        if [ ! -f /etc/traefik/acme.json ]; then
          touch /etc/traefik/acme.json
          chmod 600 /etc/traefik/acme.json
        fi
        chown traefik:traefik /etc/traefik/acme.json
        """,
    ],
)

# --- Static config ---

static_yaml = f"""\
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint:
          to: websecure
          scheme: https
          permanent: true
  websecure:
    address: ":443"

certificatesResolvers:
  cloudflare:
    acme:
      email: "admin@{DOMAIN}"
      storage: /etc/traefik/acme.json
      dnsChallenge:
        provider: cloudflare
        resolvers:
          - "1.1.1.1:53"
          - "8.8.8.8:53"

providers:
  file:
    directory: /etc/traefik/dynamic
    watch: true

log:
  level: WARN

api:
  dashboard: false
"""

files.put(
    name="Write Traefik static config",
    src=io.BytesIO(static_yaml.encode()),
    dest="/etc/traefik/static.yaml",
    user="root",
    group="root",
    mode="644",
)

# --- Dynamic config ---

dynamic_yaml = f"""\
http:
  routers:
    hcc:
      rule: "Host(`hcc.{DOMAIN}`)"
      service: hcc
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare
        domains:
          - main: "{DOMAIN}"
            sans: ["*.{DOMAIN}"]

    pihole-root:
      rule: "Host(`pihole.{DOMAIN}`) && Path(`/`)"
      service: pihole
      entryPoints: [websecure]
      middlewares: [pihole-redirect]
      tls:
        certResolver: cloudflare

    pihole:
      rule: "Host(`pihole.{DOMAIN}`)"
      service: pihole
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    audiobooks:
      rule: "Host(`audiobooks.{DOMAIN}`)"
      service: audiobooks
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    vpn:
      rule: "Host(`vpn.{DOMAIN}`)"
      service: vpn
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    ntfy:
      rule: "Host(`ntfy.{DOMAIN}`)"
      service: ntfy
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    status:
      rule: "Host(`status.{DOMAIN}`)"
      service: status
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    vault:
      rule: "Host(`vault.{DOMAIN}`)"
      service: vault
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    rss:
      rule: "Host(`rss.{DOMAIN}`)"
      service: rss
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    music:
      rule: "Host(`music.{DOMAIN}`)"
      service: music
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    syncthing:
      rule: "Host(`syncthing.{DOMAIN}`)"
      service: syncthing
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

  middlewares:
    pihole-redirect:
      redirectRegex:
        regex: '^https://pihole\\.{DOMAIN}/$'
        replacement: 'https://pihole.{DOMAIN}/admin'
        permanent: true

  services:
    hcc:
      loadBalancer:
        servers:
          - url: "http://{HCC["host"]}:{HCC["port"]}"

    pihole:
      loadBalancer:
        servers:
          - url: "http://{PIHOLE["host"]}:{PIHOLE["web_port"]}"

    audiobooks:
      loadBalancer:
        servers:
          - url: "http://{AUDIOBOOKSHELF["host"]}:{AUDIOBOOKSHELF["port"]}"

    vpn:
      loadBalancer:
        servers:
          - url: "http://{WGPORTAL["host"]}:{WGPORTAL["port"]}"

    ntfy:
      loadBalancer:
        servers:
          - url: "http://{NTFY["host"]}:{NTFY["port"]}"

    status:
      loadBalancer:
        servers:
          - url: "http://{GATUS["host"]}:{GATUS["port"]}"

    vault:
      loadBalancer:
        servers:
          - url: "http://{VAULTWARDEN["host"]}:{VAULTWARDEN["port"]}"

    rss:
      loadBalancer:
        servers:
          - url: "http://{YARR["host"]}:{YARR["port"]}"

    music:
      loadBalancer:
        servers:
          - url: "http://{NAVIDROME["host"]}:{NAVIDROME["port"]}"

    syncthing:
      loadBalancer:
        servers:
          - url: "http://{SYNCTHING["host"]}:{SYNCTHING["port"]}"
"""

files.put(
    name="Write Traefik dynamic config",
    src=io.BytesIO(dynamic_yaml.encode()),
    dest="/etc/traefik/dynamic/services.yaml",
    user="root",
    group="root",
    mode="644",
)

# --- systemd service ---

service_unit = """\
[Unit]
Description=Traefik reverse proxy
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=traefik
EnvironmentFile=/etc/secrets/cloudflare.env
ExecStart=/usr/local/bin/traefik --configFile=/etc/traefik/static.yaml
Restart=always
RestartSec=5
NoNewPrivileges=true
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
AmbientCapabilities=CAP_NET_BIND_SERVICE
MemoryMax=64M
ProtectSystem=strict
ReadWritePaths=/etc/traefik
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
LockPersonality=yes

[Install]
WantedBy=multi-user.target
"""

files.put(
    name="Write traefik systemd unit",
    src=io.BytesIO(service_unit.encode()),
    dest="/etc/systemd/system/traefik.service",
    user="root",
    group="root",
    mode="644",
)

_static_hash = hashlib.sha256((static_yaml + service_unit).encode()).hexdigest()

systemd.service(
    name="Enable Traefik",
    service="traefik",
    enabled=True,
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart Traefik if config changed",
    commands=[
        f"""
        STAMP=/etc/traefik/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_static_hash}" ]; then
          systemctl restart traefik
          echo '{_static_hash}' > "$STAMP"
        fi
        """,
    ],
)
