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

## The internal-only allowlist

Public 443 is forwarded to this process so the NetBird coordinator is reachable
from the internet. Traefik matches on Host header, not source IP, so without a
filter every vhost on the box would answer anyone who sets the header — including
the ones with no authentication of their own (halo, ntfy, tracker, party, dice,
the zot registry, and the Mac mini's ai/comfy/stt/tts upstreams).

So `internal-only` (an ipAllowList over the LAN, both NetBird mesh ranges, and
loopback) is attached to **every** router by default, and only the subdomains in
TRAEFIK["public_hosts"] opt out. The polarity is the point: a route added later
without thinking about it is LAN-only, not silently internet-facing. Anything
generated outside the ROUTES loop — the required pihole/idm/auth blocks and the
per-service `*-monitor` bypass routers — has to opt in explicitly, which is why
`_router_block` is not the only place the middleware appears.
"""

import hashlib
import io
import re

from pyinfra.operations import files, server, systemd

import vault
from group_data.all import (
    KANIDM,
    KANIDM_OIDC_CLIENTS,
    NETBIRD,
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
DICE = optional("DICE")
GATUS = optional("GATUS")
HALO = optional("HALO")
MCP_CHAT = optional("MCP_CHAT")
MEMOS = optional("MEMOS")
NAVIDROME = optional("NAVIDROME")
NIB = optional("NIB")
NTFY = optional("NTFY")
PARTY = optional("PARTY")
OCULAR = optional("OCULAR")
RASPI_DASHBOARD = optional("RASPI_DASHBOARD")
REPRESENT = optional("REPRESENT")
SCRIBE = optional("SCRIBE")
SHELF = optional("SHELF")
STT = optional("STT")
SUPERSAW = optional("SUPERSAW")
SYNCTHING = optional("SYNCTHING")
TRACKER = optional("TRACKER")
TTS = optional("TTS")
VAULTWARDEN = optional("VAULTWARDEN")
YARR = optional("YARR")
ZOT = optional("ZOT")

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
    ("ntfy", NTFY, "ntfy"),
    ("gatus", GATUS, "gatus"),
    ("vault", VAULTWARDEN, "vault"),
    ("rss", YARR, "rss"),
    ("music", NAVIDROME, "music"),
    # tracker is intentionally NOT in _gated_hosts — LAN-only, no oauth2-proxy
    # (the backend runs with TRACKER_OPEN=1). LAN reachability via internal DNS.
    ("tracker", TRACKER, "tracker"),
    # party: same LAN-only model as tracker (PARTY_OPEN=1, NOT gated). The
    # transcoder is loopback-only and intentionally NOT routed.
    ("party", PARTY, "party"),
    # dice: public/un-gated (no login at all) — intentionally NOT in
    # _gated_hosts. /ws is same-origin, so a plain HTTP router forwards the
    # WebSocket upgrade with no extra config.
    ("dice", DICE, "dice"),
    ("memo", MEMOS, "memo"),
    ("chat", CHAT, "chat"),
    ("represent", REPRESENT, "represent"),
    # nib: runs its own Kanidm OIDC client, so it is intentionally NOT in
    # _gated_hosts — oauth2-proxy forward-auth would 401 its /mcp surface, which
    # authenticates with a per-user bearer token and carries no session cookie.
    # /ws is same-origin, so the plain HTTP router forwards the upgrade as-is.
    ("nib", NIB, "nib"),
    ("scribe", SCRIBE, "scribe"),
    ("shelf", SHELF, "shelf"),
    ("syncthing", SYNCTHING, "syncthing"),
    ("beszel", BESZEL, "beszel"),
    ("dashboard", RASPI_DASHBOARD, "dashboard"),
    ("supersaw", SUPERSAW, "supersaw"),
    # Private OCI registry — intentionally NOT in _gated_hosts, so no oauth2
    # forward-auth (podman can't do interactive SSO). LAN-only via internal DNS.
    ("registry", ZOT, "registry"),
    ("ocular", OCULAR, "ocular"),
    ("ai", AI, "ai"),
    ("comfy", COMFY, "comfy"),
    ("stt", STT, "stt"),
    ("tts", TTS, "tts"),
    ("mcp-chat", MCP_CHAT, "mcp-chat"),
]


# Hostnames the allowlist does not apply to. Keyed on the subdomain rather than
# the router name on purpose: reachability is a property of the vhost, and one
# vhost can need several routers (netbird needs three — gRPC, backend, dashboard).
# Keying on router names would silently guard two of those three.
PUBLIC_HOSTS = frozenset(TRAEFIK.get("public_hosts", ()))

# Sources allowed to reach a non-public vhost: the LAN, both NetBird mesh ranges
# (a connected peer is as trusted as a LAN client), and loopback for the Pi's own
# fan-in (raspi-dashboard reading gatus, gatus probing everything else).
_INTERNAL_CIDRS = (
    NETWORK["lan_cidr"],
    NETBIRD["account_settings"]["network_range"],
    NETBIRD["account_settings"]["network_range_v6"],
    "127.0.0.1/32",
    "::1/128",
)


def _guard(host_prefix, middlewares=()):
    """Middleware list for a router, with the internal-only allowlist prepended
    unless its vhost is listed in TRAEFIK["public_hosts"]. Deny-by-default: every
    caller gets the guard for free and has to opt out by name."""
    if host_prefix in PUBLIC_HOSTS:
        return list(middlewares)
    return ["internal-only", *middlewares]


def _router_block(name, prefix, aliases=(), middlewares=(), *, service=None, priority=None):
    hosts = " || ".join(f"Host(`{p}.{DOMAIN}`)" for p in (prefix, *aliases))
    lines = [
        f"    {name}:",
        f'      rule: "{hosts}"',
        f"      service: {service or name}",
        "      entryPoints: [websecure]",
    ]
    if priority is not None:
        lines.append(f"      priority: {priority}")
    _mws = _guard(prefix, middlewares)
    if _mws:
        lines.append(f"      middlewares: [{', '.join(_mws)}]")
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

# Errors only, to stdout -> journald. With 443 reachable from the internet there
# was previously no record of who hit the proxy at all; a 403 from the
# internal-only allowlist is exactly the signal worth keeping. Filtered to 4xx/5xx
# so normal traffic (including media streaming) writes nothing, and journald here
# is Storage=volatile with RuntimeMaxUse=64M (files/journald.conf), so this costs
# RAM that is already bounded rather than SD-card writes.
accessLog:
  format: json
  filters:
    statusCodes:
      - "400-499"
      - "500-599"

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


def _mw_yaml(host_prefix, extra=()):
    """The `middlewares:` line for a hand-written router block. Goes through
    _guard, so these routers pick up the internal-only allowlist on the same
    deny-by-default terms as the generated ones."""
    mws = _guard(host_prefix, extra)
    return f"\n      middlewares: [{', '.join(mws)}]" if mws else ""


# Required routers — always emitted.
_required_routers = f"""\
    # Unauthenticated Pi-hole API path used by Gatus uptime checks.
    pihole-monitor:
      rule: "Host(`pihole.{DOMAIN}`) && Path(`/api/info/version`)"
      service: pihole
      priority: 100
      entryPoints: [websecure]{_mw_yaml("pihole")}
      tls:
        certResolver: cloudflare

    pihole-root:
      rule: "Host(`pihole.{DOMAIN}`) && Path(`/`)"
      service: pihole
      entryPoints: [websecure]{_mw_yaml("pihole", ["oauth2-chain-pihole", "pihole-redirect"])}
      tls:
        certResolver: cloudflare

    pihole:
      rule: "Host(`pihole.{DOMAIN}`)"
      service: pihole
      entryPoints: [websecure]{_mw_yaml("pihole", ["oauth2-chain-pihole"])}
      tls:
        certResolver: cloudflare

    # idm/Kanidm is always present, so the wildcard cert declaration lives here
    # — every other subdomain is served the same `*.{DOMAIN}` cert.
    #
    # Allowlisted like everything else, which means a browser that is neither on
    # the LAN nor on the mesh cannot reach the login page. That is deliberate:
    # off-LAN peers enrol with a setup key, not an interactive SSO round-trip. Add
    # "idm" to TRAEFIK["public_hosts"] to trade that for remote browser login.
    idm:
      rule: "Host(`idm.{DOMAIN}`)"
      service: idm
      entryPoints: [websecure]{_mw_yaml("idm")}
      tls:
        certResolver: cloudflare
        domains:
          - main: "{DOMAIN}"
            sans: ["*.{DOMAIN}"]

    auth:
      rule: "Host(`auth.{DOMAIN}`)"
      service: auth
      entryPoints: [websecure]{_mw_yaml("auth")}
      tls:
        certResolver: cloudflare

    # --- NetBird coordinator: the one intentionally internet-facing vhost ---
    #
    # Three routers because the backend speaks three protocols on one port. The
    # gRPC paths need an h2c (HTTP/2 cleartext) upstream or agent registration
    # fails with a content-type error; /relay and /ws-proxy are WebSocket upgrades,
    # which a plain HTTP router forwards as-is; everything left over is the
    # dashboard SPA, so it is a priority-1 catch-all that must lose to the other
    # two. STUN is UDP and is not here at all — it cannot be proxied.
    netbird-grpc:
      rule: "Host(`{NETBIRD["url_prefix"]}.{DOMAIN}`) && (PathPrefix(`/management.ManagementService/`) || PathPrefix(`/management.ProxyService/`) || PathPrefix(`/signalexchange.SignalExchange/`))"
      service: netbird-h2c
      priority: 100
      entryPoints: [websecure]{_mw_yaml(NETBIRD["url_prefix"])}
      tls:
        certResolver: cloudflare

    netbird-backend:
      rule: "Host(`{NETBIRD["url_prefix"]}.{DOMAIN}`) && (PathPrefix(`/api`) || PathPrefix(`/oauth2`) || PathPrefix(`/relay`) || PathPrefix(`/ws-proxy/`))"
      service: netbird
      priority: 100
      entryPoints: [websecure]{_mw_yaml(NETBIRD["url_prefix"])}
      tls:
        certResolver: cloudflare

    netbird:
      rule: "Host(`{NETBIRD["url_prefix"]}.{DOMAIN}`)"
      service: netbird-dashboard
      priority: 1
      entryPoints: [websecure]{_mw_yaml(NETBIRD["url_prefix"])}
      tls:
        certResolver: cloudflare"""

# Unauthenticated Syncthing health endpoints used by Gatus uptime checks —
# only meaningful when Syncthing is deployed.
_syncthing_monitor = f"""\
    syncthing-monitor:
      rule: "Host(`syncthing.{DOMAIN}`) && PathPrefix(`/rest/noauth`)"
      service: syncthing
      priority: 100
      entryPoints: [websecure]{_mw_yaml("syncthing")}
      tls:
        certResolver: cloudflare"""

# Unauthenticated ocular liveness endpoint for Gatus — bypasses oauth2 so the
# probe isn't redirected to the login page.
_ocular_monitor = f"""\
    ocular-monitor:
      rule: "Host(`ocular.{DOMAIN}`) && Path(`/status`)"
      service: ocular
      priority: 100
      entryPoints: [websecure]{_mw_yaml("ocular")}
      tls:
        certResolver: cloudflare"""

_routers = [_required_routers]
_services = [
    _service_block("pihole", f"http://{PIHOLE['host']}:{PIHOLE['web_port']}"),
    _service_block(
        "idm", f"https://{KANIDM['host']}:{KANIDM['port']}", transport="kanidmTransport"
    ),
    _service_block("auth", f"http://{OAUTH2_PROXY['host']}:{OAUTH2_PROXY['port']}"),
    # Same upstream twice: `h2c://` is how Traefik v3 is told to speak HTTP/2
    # cleartext to a backend, which the gRPC routers require and the plain HTTP
    # routers must not use.
    _service_block("netbird", f"http://{NETBIRD['host']}:{NETBIRD['port']}"),
    _service_block("netbird-h2c", f"h2c://{NETBIRD['host']}:{NETBIRD['port']}"),
    _service_block("netbird-dashboard", f"http://{NETBIRD['host']}:{NETBIRD['dashboard_port']}"),
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

_internal_cidr_yaml = "\n".join(f"          - {cidr}" for cidr in _INTERNAL_CIDRS)

_middlewares = f"""\
    # Attached to every router whose vhost is not in TRAEFIK["public_hosts"].
    # Public 443 reaches this process from the internet (for the NetBird
    # coordinator), and Traefik routes on Host header rather than source IP — so
    # this is the only thing standing between a spoofed Host header and the
    # services on this box that have no auth of their own. Traefik sees the real
    # client IP directly (nothing sits in front of it), so no depth/xff handling
    # is needed here.
    internal-only:
      ipAllowList:
        sourceRange:
{_internal_cidr_yaml}
    compress:
      compress:
        # A whitelist, not a blacklist: anything absent is never compressed.
        #
        # This middleware hangs off the websecure entrypoint (see static_yaml),
        # so it applies to every HTTPS route on the box — including the audio
        # streams from shelf, audiobookshelf and navidrome. gzip on a media
        # response strips Content-Length and Accept-Ranges, and leaves the ETag
        # describing bytes that are no longer what's on the wire. Those are
        # exactly the headers a client needs in order to resume an interrupted
        # download, so compressing an m4b silently breaks resume — and buys
        # nothing, since AAC is already compressed.
        #
        # The direction matters: omitting a text type here costs a little
        # bandwidth, while omitting a binary type from a blacklist breaks
        # resume again with no signal. So: text types only, and extend this
        # list rather than switching to excludedContentTypes (Traefik treats
        # the two as mutually exclusive).
        #
        # text/javascript, not just application/javascript: the former is what
        # mime_guess (and WHATWG) give a .js file, so it's what the SPA
        # backends actually serve.
        includedContentTypes:
          - text/html
          - text/css
          - text/plain
          - text/xml
          - text/javascript
          - application/javascript
          - application/json
          - application/xml
          - application/rss+xml
          - application/atom+xml
          - image/svg+xml
    pihole-redirect:
      redirectRegex:
        regex: '^https://pihole\\.{DOMAIN}/$'
        replacement: 'https://pihole.{DOMAIN}/admin'
        permanent: true
    oauth2-proxy:
      forwardAuth:
        address: "http://{OAUTH2_PROXY["host"]}:{OAUTH2_PROXY["port"]}/oauth2/auth"
        trustForwardHeader: true
        # Bound what Traefik will buffer from the auth server. Left unset it
        # accepts an unlimited body, which Traefik itself warns is a memory
        # exhaustion path — and this box has 1 GB with no swap headroom to spare.
        # /oauth2/auth answers with headers and at most a session cookie, so 64 KB
        # is already generous; the only way to exceed it is oauth2-proxy
        # misbehaving, which is precisely the case worth failing closed on.
        maxResponseBodySize: 65536
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
    # Named `default`, so it applies to every router without touching any of them.
    # sniStrict is the interesting half: without it a connection with no matching
    # SNI (a scanner dialling the bare public IP) is served Traefik's built-in
    # self-signed cert, which both answers and fingerprints. With it the handshake
    # simply fails. Nothing here talks to Traefik by IP over HTTPS — the
    # https://127.0.0.1 calls in tasks/kanidm*.py go straight to Kanidm on :8443,
    # not through the proxy — so nothing legitimate loses its connection.
    + "\ntls:\n"
    + "  options:\n"
    + "    default:\n"
    + "      minVersion: VersionTLS12\n"
    + "      sniStrict: true\n"
)


def _assert_routers_guarded(yaml_text):
    """Fail at plan time if any router lacks the internal-only allowlist.

    `_router_block` attaches it for free, but the hand-written blocks each call
    `_mw_yaml` by hand. Forgetting one on a box whose 443 is forwarded from the
    internet is silent — no error, no symptom, just an exposed vhost — so the
    check is mechanical rather than a code-review habit. A router serving several
    hosts must have *all* of them public to skip the guard, otherwise an alias
    could smuggle a private name onto a public router.
    """
    section = yaml_text.split("  routers:\n", 1)[1].split("\n  middlewares:", 1)[0]
    for name, body in re.findall(r"^    ([A-Za-z0-9_-]+):\n((?:^ {6,}.*\n)+)", section, re.M):
        hosts = re.findall(rf"Host\(`([^`]+)\.{re.escape(DOMAIN)}`\)", body)
        if "internal-only" in body:
            continue
        if hosts and all(h in PUBLIC_HOSTS for h in hosts):
            continue
        raise ValueError(
            f"traefik: router '{name}' (hosts: {hosts or 'unknown'}) has no "
            "`internal-only` middleware and is not fully covered by "
            'TRAEFIK["public_hosts"]. With public 443 forwarded to this proxy that '
            "would expose it to the internet. Add _mw_yaml(<host_prefix>) to the "
            "block, or list the host in public_hosts if that is genuinely intended."
        )


_assert_routers_guarded(dynamic_yaml)

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
