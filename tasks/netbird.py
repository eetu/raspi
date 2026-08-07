"""NetBird coordinator: netbird-server + dashboard as Podman Quadlet units.

`netbirdio/netbird-server` is the combined container (v0.65.0+) — management,
signal, relay, STUN and the embedded IdP (Dex) in one process, SQLite store. Only
the dashboard ships separately.

`NB_SETUP_PAT_ENABLED=true` is what makes an unattended install possible: it lets
POST /api/setup return a plaintext API token, which tasks/netbird_bootstrap.py
captures into the vault. The endpoint only answers while no account exists, so the
flag is inert after the first deploy.

Two networking choices worth knowing:

  - The server runs `Network=host`, like every other quadlet here, because the
    embedded IdP has to resolve and reach idm.{domain} to federate Kanidm (Dex
    fetches the discovery document and JWKS itself). It cannot be pinned to
    loopback: the combined server extracts only the *port* from `listenAddress`
    (combined/cmd/root.go does `net.SplitHostPort` and discards the host), so it
    binds 0.0.0.0. ufw's default-deny inbound is what keeps 8081/9091/9092 off the
    LAN — only 443 (Traefik) and 3478/udp (STUN) are reachable.
  - The dashboard is a bridge-net container publishing on loopback. It is static
    files whose every URL is dialled by the browser, so it needs no DNS, and this
    keeps it off :80 where Traefik lives.

Memory is the real constraint on a 1 GB Pi — this is the largest process on the
box. GOMEMLIMIT caps the Go heap so the runtime collects rather than growing into
swap, and MemoryMax is the hard backstop. NetBird's own docs ask for a 2 GB host,
so check `systemd-cgtop --order=memory` after deploying.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault
from group_data.all import NETWORK
from tasks.util import optional, restart_if_changed

NETBIRD = optional("NETBIRD")

_UNITS = ("netbird-server", "netbird-dashboard")


if NETBIRD is None:
    # Retired: keep state on disk, just stop + disable the units so the ports are
    # freed. Re-adding the dict restores the coordinator with every peer intact.
    for _unit in _UNITS:
        systemd.service(
            name=f"Stop + disable {_unit} (kept on disk for rollback)",
            service=_unit,
            running=False,
            enabled=False,
            daemon_reload=True,
        )
else:
    DOMAIN = NETWORK["domain"]
    PUBLIC_URL = f"https://{NETBIRD['url_prefix']}.{DOMAIN}"
    CONFIG_PATH = f"{NETBIRD['install_dir']}/config.yaml"
    DASHBOARD_ENV = f"{NETBIRD['install_dir']}/dashboard.env"

    _secrets = vault.netbird_server_secrets()

    # --- Directories ---

    for _path in (NETBIRD["install_dir"], NETBIRD["data_dir"]):
        files.directory(
            name=f"Create {_path}",
            path=_path,
            user="root",
            group="root",
            mode="750",
            present=True,
        )

    # --- Server config ---
    #
    # Carries authSecret + store.encryptionKey, so it is a 600 secret file
    # rendered from the vault (the shape the retired wg-portal config.yml used).
    # Rotating any of them changes the rendered string, which moves the restart
    # fingerprint below.
    #
    # `exposedAddress` is what peers are told to dial — the public URL on 443. It
    # is the relay's `rels://` endpoint and the management DNS domain, so it must
    # never be a local address, however the server itself is bound.
    config_yaml = f"""\
server:
  listenAddress: ":{NETBIRD["port"]}"
  exposedAddress: "{PUBLIC_URL}:443"
  stunPorts:
    - {NETBIRD["stun_port"]}
  metricsPort: {NETBIRD["metrics_port"]}
  healthcheckAddress: ":{NETBIRD["health_port"]}"
  logLevel: "{NETBIRD["log_level"]}"
  logFile: "console"
  authSecret: "{_secrets["auth_secret"]}"
  dataDir: "/var/lib/netbird"
  # Nothing phones home and no GeoLite database is fetched — both are pure cost
  # here (the GeoLite download alone is larger than the container image).
  disableAnonymousMetrics: true
  disableGeoliteUpdate: true

  # Traefik terminates TLS and is the only thing that connects over HTTP, so
  # trust its forwarded client IP. Without this every peer looks like 127.0.0.1.
  reverseProxy:
    trustedHTTPProxies:
      - "127.0.0.1/32"
    trustedPeers:
      - "{NETBIRD["account_settings"]["network_range"]}"
      - "{NETBIRD["account_settings"]["network_range_v6"]}"

  auth:
    issuer: "{PUBLIC_URL}/oauth2"
    # When true the embedded IdP stops offering its email/password connector, so
    # Kanidm is the only way to sign in. The bootstrap owner still exists as an API
    # identity — it just cannot log in, which is the point. See the rebuild caveat
    # on NETBIRD["local_auth_disabled"] in group_data/all.py.
    localAuthDisabled: {"true" if NETBIRD.get("local_auth_disabled") else "false"}
    signKeyRefreshEnabled: true
    sessionCookieEncryptionKey: "{_secrets["session_cookie_encryption_key"]}"
    dashboardRedirectURIs:
      - "{PUBLIC_URL}/nb-auth"
      - "{PUBLIC_URL}/nb-silent-auth"
    cliRedirectURIs:
      - "http://localhost:53000/"

  store:
    engine: "sqlite"
    dsn: ""
    encryptionKey: "{_secrets["encryption_key"]}"
"""

    files.put(
        name="Write netbird-server config.yaml",
        src=io.BytesIO(config_yaml.encode()),
        dest=CONFIG_PATH,
        user="root",
        group="root",
        mode="600",
    )

    # --- Dashboard env ---
    #
    # Config is baked into the SPA at container start. Every URL here is the
    # public one because the browser, not the container, makes these calls.
    dashboard_env = f"""\
NETBIRD_MGMT_API_ENDPOINT={PUBLIC_URL}
NETBIRD_MGMT_GRPC_API_ENDPOINT={PUBLIC_URL}
AUTH_AUDIENCE=netbird-dashboard
AUTH_CLIENT_ID=netbird-dashboard
AUTH_CLIENT_SECRET=
AUTH_AUTHORITY={PUBLIC_URL}/oauth2
USE_AUTH0=false
AUTH_SUPPORTED_SCOPES=openid profile email groups
AUTH_REDIRECT_URI=/nb-auth
AUTH_SILENT_REDIRECT_URI=/nb-silent-auth
NGINX_SSL_PORT=443
LETSENCRYPT_DOMAIN=none
"""

    files.put(
        name="Write netbird dashboard.env",
        src=io.BytesIO(dashboard_env.encode()),
        dest=DASHBOARD_ENV,
        user="root",
        group="root",
        mode="644",
    )

    # --- Quadlets ---

    server_quadlet = f"""\
[Unit]
Description=NetBird coordinator (management + signal + relay + STUN + embedded IdP)
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=netbird-server
Image={NETBIRD["server_image"]}
Exec=--config /etc/netbird/config.yaml
Network=host
Volume={NETBIRD["data_dir"]}:/var/lib/netbird
Volume={CONFIG_PATH}:/etc/netbird/config.yaml:ro
Environment=TZ=Europe/Helsinki
# Enables the plaintext-token response from POST /api/setup. Only has any effect
# while no account exists — see tasks/netbird_bootstrap.py.
Environment=NB_SETUP_PAT_ENABLED=true
Environment=NB_DISABLE_GEOLOCATION=true
# Tell the Go runtime its ceiling so it collects instead of growing into swap.
# Kept under MemoryMax so GC pressure arrives before the OOM killer does.
Environment=GOMEMLIMIT=256MiB

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=320M

[Install]
WantedBy=multi-user.target
"""

    dashboard_quadlet = f"""\
[Unit]
Description=NetBird dashboard
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=netbird-dashboard
Image={NETBIRD["dashboard_image"]}
# Bridge net, not Network=host: the image's nginx listens on :80, which Traefik
# already owns. Publishing on loopback also keeps it genuinely unreachable from
# the LAN, which the server container cannot manage.
PublishPort={NETBIRD["host"]}:{NETBIRD["dashboard_port"]}:80
EnvironmentFile={DASHBOARD_ENV}

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=48M

[Install]
WantedBy=multi-user.target
"""

    for _unit, _quadlet in (
        ("netbird-server", server_quadlet),
        ("netbird-dashboard", dashboard_quadlet),
    ):
        files.put(
            name=f"Write {_unit}.container quadlet",
            src=io.BytesIO(_quadlet.encode()),
            dest=f"/etc/containers/systemd/{_unit}.container",
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

    for _unit in _UNITS:
        systemd.service(
            name=f"Start {_unit}",
            service=_unit,
            running=True,
            daemon_reload=True,
        )

    # The config is bind-mounted read-only, so the process only picks up changes
    # on restart — hash it together with the quadlet.
    _server_hash = hashlib.sha256((server_quadlet + config_yaml).encode()).hexdigest()
    _dashboard_hash = hashlib.sha256((dashboard_quadlet + dashboard_env).encode()).hexdigest()

    server.shell(
        name="Restart netbird-server if quadlet or config changed",
        commands=[restart_if_changed("netbird-server", _server_hash)],
    )

    server.shell(
        name="Restart netbird-dashboard if quadlet or env changed",
        commands=[restart_if_changed("netbird-dashboard", _dashboard_hash)],
    )
