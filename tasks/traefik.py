"""Traefik: download binary, static + dynamic config, systemd service.

The dynamic config is generated from a route registry (ROUTES below).
Required routes — pihole, idm (Kanidm), auth (oauth2-proxy) — are always
emitted. Every other route is gated on an optional() service dict: comment
the dict in group_data/all.py and its router + service disappear from the
generated YAML, so a retired service stops being reverse-proxied without
any edit here.

The wildcard TLS cert (`*.{domain}`) is declared on the idm router because
idm/Kanidm is always present — that keeps a single DNS-01 wildcard covering
every subdomain regardless of which optional services are deployed.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault
from group_data.all import (
    KANIDM,
    KANIDM_OIDC_CLIENTS,
    NETWORK,
    OAUTH2_PROXY,
    PIHOLE,
    TRAEFIK,
)
from tasks.util import optional, restart_if_changed

# Optional service dicts — None when retired (commented in group_data/all.py).
# A route whose dict is None is skipped by the generator below.
AI = optional("AI")
AUDIOBOOKSHELF = optional("AUDIOBOOKSHELF")
BESZEL = optional("BESZEL")
CHAT = optional("CHAT")
COMFY = optional("COMFY")
GATUS = optional("GATUS")
HALO = optional("HALO")
MCP_CHAT = optional("MCP_CHAT")
MEMOS = optional("MEMOS")
NAVIDROME = optional("NAVIDROME")
NTFY = optional("NTFY")
OCULAR = optional("OCULAR")
RASPI_DASHBOARD = optional("RASPI_DASHBOARD")
REPRESENT = optional("REPRESENT")
SCRIBE = optional("SCRIBE")
SHELF = optional("SHELF")
STT = optional("STT")
SUPERSAW = optional("SUPERSAW")
SYNCTHING = optional("SYNCTHING")
TTS = optional("TTS")
VAULTWARDEN = optional("VAULTWARDEN")
WGPORTAL = optional("WGPORTAL")
YARR = optional("YARR")

VERSION = TRAEFIK["version"]
BINARY_URL = (
    f"https://github.com/traefik/traefik/releases/download/{VERSION}/"
    f"traefik_{VERSION}_linux_arm64.tar.gz"
)
DOMAIN = NETWORK["domain"]

# Whether oauth2-proxy is wired up for this deployment. Used to gate the
# music router — when oauth2-proxy is not configured, Navidrome is exposed
# directly and clients use its native username/password auth instead of IAP.
_oauth2_client = KANIDM_OIDC_CLIENTS.get("oauth2-proxy")
_oauth2_active = bool(_oauth2_client and vault.kanidm_oidc_secret(_oauth2_client["secret_field"]))

# Hosts fronted by an oauth2-proxy forward-auth chain. Each gets a per-host
# errors middleware whose `rd` pins the post-auth redirect target. pihole is
# required so it's always present; the rest only appear when their service is
# deployed. music additionally requires oauth2-proxy to be active (otherwise
# Navidrome is exposed directly with its own auth).
_gated_hosts = ["pihole"]
if YARR:
    _gated_hosts.append("rss")
if SYNCTHING:
    _gated_hosts.append("syncthing")
if NAVIDROME and _oauth2_active:
    _gated_hosts.append("music")
# Gatus no longer runs its own OIDC (tasks/gatus.py) — its server is open on
# loopback for raspi-dashboard to fan in, so the human-facing route must be
# gated here. Requires oauth2-proxy; without it gatus would be exposed directly.
if GATUS and _oauth2_active:
    _gated_hosts.append("gatus")
# raspi-dashboard has no own login — it relies entirely on oauth2-proxy at the
# edge. Always gate it when oauth2-proxy is active.
if RASPI_DASHBOARD and _oauth2_active:
    _gated_hosts.append("dashboard")
# ocular runs on the camera node (raspo); raspi only proxies it. SSO-gate the
# human route; the /status monitor router below bypasses oauth2 for gatus.
if OCULAR and _oauth2_active:
    _gated_hosts.append("ocular")
# supersaw is a static SPA with no auth of its own — gate it at the edge.
if SUPERSAW and _oauth2_active:
    _gated_hosts.append("supersaw")

# Optional route registry: (router/service name, gating dict, default subdomain).
# The subdomain prefix comes from the dict's own `url_prefix` when set
# (scribe/shelf/ai/comfy/stt/tts/mcp-chat), otherwise the default here (for
# services whose public name is owned by their Kanidm OIDC client instead, e.g.
# vault/vpn/status/metrics/memo). Aliases are read from the dict's `aliases`.
ROUTES = [
    ("halo", HALO, "halo"),
    ("audiobooks", AUDIOBOOKSHELF, "audiobooks"),
    ("vpn", WGPORTAL, "vpn"),
    ("ntfy", NTFY, "ntfy"),
    ("gatus", GATUS, "gatus"),
    ("vault", VAULTWARDEN, "vault"),
    ("rss", YARR, "rss"),
    ("music", NAVIDROME, "music"),
    ("memo", MEMOS, "memo"),
    ("chat", CHAT, "chat"),
    ("represent", REPRESENT, "represent"),
    ("scribe", SCRIBE, "scribe"),
    ("shelf", SHELF, "shelf"),
    ("syncthing", SYNCTHING, "syncthing"),
    ("beszel", BESZEL, "beszel"),
    ("dashboard", RASPI_DASHBOARD, "dashboard"),
    ("supersaw", SUPERSAW, "supersaw"),
    ("ocular", OCULAR, "ocular"),
    ("ai", AI, "ai"),
    ("comfy", COMFY, "comfy"),
    ("stt", STT, "stt"),
    ("tts", TTS, "tts"),
    ("mcp-chat", MCP_CHAT, "mcp-chat"),
]


def _router_block(name, prefix, aliases=(), middlewares=()):
    hosts = " || ".join(f"Host(`{p}.{DOMAIN}`)" for p in (prefix, *aliases))
    lines = [
        f"    {name}:",
        f'      rule: "{hosts}"',
        f"      service: {name}",
        "      entryPoints: [websecure]",
    ]
    if middlewares:
        lines.append(f"      middlewares: [{', '.join(middlewares)}]")
    lines += ["      tls:", "        certResolver: cloudflare"]
    return "\n".join(lines)


def _service_block(name, url, transport=None):
    lines = [
        f"    {name}:",
        "      loadBalancer:",
        "        servers:",
        f'          - url: "{url}"',
    ]
    if transport:
        lines.append(f"        serversTransport: {transport}")
    return "\n".join(lines)


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

# --- Dynamic config (generated from ROUTES) ---

# Required routers — always emitted.
_required_routers = f"""\
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

    # idm/Kanidm is always present, so the wildcard cert declaration lives here
    # — every other subdomain is served the same `*.{DOMAIN}` cert.
    idm:
      rule: "Host(`idm.{DOMAIN}`)"
      service: idm
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare
        domains:
          - main: "{DOMAIN}"
            sans: ["*.{DOMAIN}"]

    auth:
      rule: "Host(`auth.{DOMAIN}`)"
      service: auth
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare"""

# Unauthenticated Syncthing health endpoints used by Gatus uptime checks —
# only meaningful when Syncthing is deployed.
_syncthing_monitor = f"""\
    syncthing-monitor:
      rule: "Host(`syncthing.{DOMAIN}`) && PathPrefix(`/rest/noauth`)"
      service: syncthing
      priority: 100
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare"""

# Unauthenticated ocular liveness endpoint for Gatus — bypasses oauth2 so the
# probe isn't redirected to the login page.
_ocular_monitor = f"""\
    ocular-monitor:
      rule: "Host(`ocular.{DOMAIN}`) && Path(`/status`)"
      service: ocular
      priority: 100
      entryPoints: [websecure]
      tls:
        certResolver: cloudflare"""

_routers = [_required_routers]
_services = [
    _service_block("pihole", f"http://{PIHOLE['host']}:{PIHOLE['web_port']}"),
    _service_block(
        "idm", f"https://{KANIDM['host']}:{KANIDM['port']}", transport="kanidmTransport"
    ),
    _service_block("auth", f"http://{OAUTH2_PROXY['host']}:{OAUTH2_PROXY['port']}"),
]

for _name, _cfg, _default_prefix in ROUTES:
    if _cfg is None:
        continue
    _prefix = _cfg.get("url_prefix") or _default_prefix
    _aliases = _cfg.get("aliases", ())
    _mws = [f"oauth2-chain-{_name}"] if _name in _gated_hosts else []
    if _name == "syncthing":
        _routers.append(_syncthing_monitor)
    if _name == "ocular":
        _routers.append(_ocular_monitor)
    _routers.append(_router_block(_name, _prefix, _aliases, _mws))
    _services.append(_service_block(_name, f"http://{_cfg['host']}:{_cfg['port']}"))

# Per-host oauth2 chains, one set per gated host actually present.
_oauth2_per_host = "\n".join(
    f"""\
    oauth2-errors-{h}:
      errors:
        status: ["401"]
        service: auth
        query: "/oauth2/sign_in?rd=https%3A%2F%2F{h}.{DOMAIN}%2F"
    oauth2-chain-{h}:
      chain:
        middlewares: [oauth2-errors-{h}, oauth2-proxy]"""
    for h in _gated_hosts
)

_middlewares = f"""\
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
          - Set-Cookie
{_oauth2_per_host}"""

dynamic_yaml = (
    "http:\n"
    "  routers:\n"
    + "\n\n".join(_routers)
    + "\n\n  middlewares:\n"
    + _middlewares
    + "\n\n  services:\n"
    + "\n\n".join(_services)
    + "\n\n"
    + "  serversTransports:\n"
    + "    kanidmTransport:\n"
    + "      # Kanidm serves the ACME wildcard cert (Let's Encrypt) — trusted by system CAs.\n"
    + "      # serverName overrides SNI so hostname verification passes on loopback.\n"
    + f'      serverName: "idm.{DOMAIN}"\n'
)

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

# Dynamic config is hot-reloaded by Traefik's file provider (watch: true), so
# it's deliberately excluded from the restart fingerprint — only static config
# + the unit force a restart.
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
