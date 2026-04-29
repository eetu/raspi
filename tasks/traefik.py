"""Traefik: download binary, static + dynamic config, systemd service."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import (
    AUDIOBOOKSHELF,
    BESZEL,
    GATUS,
    HCC,
    KANIDM,
    NAVIDROME,
    NETWORK,
    NTFY,
    OAUTH2_PROXY,
    PIHOLE,
    SYNCTHING,
    TRAEFIK,
    VAULTWARDEN,
    WGPORTAL,
    YARR,
)
from tasks.util import restart_if_changed

VERSION = TRAEFIK["version"]
BINARY_URL = (
    f"https://github.com/traefik/traefik/releases/download/{VERSION}/"
    f"traefik_{VERSION}_linux_arm64.tar.gz"
)
DOMAIN = NETWORK["domain"]

# Hosts gated by oauth2-proxy. Each gets a per-host errors middleware whose
# `rd` parameter pins the post-auth redirect target — Traefik's errors
# middleware only substitutes {status} in `query`, and X-Forwarded-Uri is not
# propagated to the auth backend, so oauth2-proxy can't reconstruct the
# origin URL on its own.
OAUTH2_GATED_HOSTS = ("pihole", "rss", "music", "syncthing")

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

_oauth2_per_host_middlewares = "".join(
    f"""\
    oauth2-errors-{h}:
      errors:
        status: ["401"]
        service: auth
        query: "/oauth2/sign_in?rd=https%3A%2F%2F{h}.{DOMAIN}%2F"
    oauth2-chain-{h}:
      chain:
        middlewares: [oauth2-errors-{h}, oauth2-proxy]
"""
    for h in OAUTH2_GATED_HOSTS
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
    http:
      middlewares:
        - compress@file

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

    # Unauthenticated Pi-hole API path used by Gatus uptime checks.
    pihole-monitor:
      rule: "Host(`pihole.{DOMAIN}`) && Path(`/api/info/version`)"
      service: pihole
      priority: 100
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    pihole-root:
      rule: "Host(`pihole.{DOMAIN}`) && Path(`/`)"
      service: pihole
      entryPoints: [websecure]
      middlewares: [oauth2-chain-pihole, pihole-redirect]
      tls:
        certResolver: cloudflare

    pihole:
      rule: "Host(`pihole.{DOMAIN}`)"
      service: pihole
      entryPoints: [websecure]
      middlewares: [oauth2-chain-pihole]
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
      middlewares: [oauth2-chain-rss]
      tls:
        certResolver: cloudflare

    # Subsonic API stays open (mobile clients use username/password); priority
    # forces this route ahead of the catch-all `music` router below.
    music-subsonic:
      rule: "Host(`music.{DOMAIN}`) && (PathPrefix(`/rest`) || PathPrefix(`/share`))"
      service: music
      priority: 100
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    music:
      rule: "Host(`music.{DOMAIN}`)"
      service: music
      entryPoints: [websecure]
      middlewares: [oauth2-chain-music]
      tls:
        certResolver: cloudflare

    # Unauthenticated Syncthing health endpoints used by Gatus uptime checks.
    syncthing-monitor:
      rule: "Host(`syncthing.{DOMAIN}`) && PathPrefix(`/rest/noauth`)"
      service: syncthing
      priority: 100
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    syncthing:
      rule: "Host(`syncthing.{DOMAIN}`)"
      service: syncthing
      entryPoints: [websecure]
      middlewares: [oauth2-chain-syncthing]
      tls:
        certResolver: cloudflare

    metrics:
      rule: "Host(`metrics.{DOMAIN}`)"
      service: metrics
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    idm:
      rule: "Host(`idm.{DOMAIN}`)"
      service: idm
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

    auth:
      rule: "Host(`auth.{DOMAIN}`)"
      service: auth
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare

  middlewares:
    compress:
      compress: {{}}
    pihole-redirect:
      redirectRegex:
        regex: '^https://pihole\\.{DOMAIN}/$'
        replacement: 'https://pihole.{DOMAIN}/admin'
        permanent: true
    oauth2-proxy:
      forwardAuth:
        address: "http://{OAUTH2_PROXY["host"]}:{OAUTH2_PROXY["port"]}/oauth2/auth"
        trustForwardHeader: true
        authResponseHeaders:
          - X-Auth-Request-User
          - X-Auth-Request-Email
{_oauth2_per_host_middlewares.rstrip()}

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

    metrics:
      loadBalancer:
        servers:
          - url: "http://{BESZEL["host"]}:{BESZEL["port"]}"

    idm:
      loadBalancer:
        servers:
          - url: "https://{KANIDM["host"]}:{KANIDM["port"]}"
        serversTransport: kanidmTransport

    auth:
      loadBalancer:
        servers:
          - url: "http://{OAUTH2_PROXY["host"]}:{OAUTH2_PROXY["port"]}"

  serversTransports:
    kanidmTransport:
      # Kanidm serves the ACME wildcard cert (Let's Encrypt) — trusted by system CAs.
      # serverName overrides SNI so hostname verification passes on loopback.
      serverName: "idm.{DOMAIN}"
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
    name="Restart Traefik if config or env changed",
    commands=[
        restart_if_changed("traefik", _static_hash, env_files=("/etc/secrets/cloudflare.env",))
    ],
)
