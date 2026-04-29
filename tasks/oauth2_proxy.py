"""oauth2-proxy: forward-auth gateway in front of select Traefik routes.

Single instance at https://auth.{domain}. Cookie domain is .{domain} so the
session is shared across all gated subdomains. Skips deploy until the Kanidm
OAuth2 client secret has been generated and saved to BW (kanidm_oidc.py runs
first; client_secret may be empty on the very first deploy).
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import KANIDM_OIDC_CLIENTS, NETWORK, OAUTH2_PROXY
from tasks.util import restart_if_changed

VERSION = OAUTH2_PROXY["version"]
DOMAIN = NETWORK["domain"]
BINARY_URL = (
    f"https://github.com/oauth2-proxy/oauth2-proxy/releases/download/{VERSION}/"
    f"oauth2-proxy-{VERSION}.linux-arm64.tar.gz"
)

_oidc_client = KANIDM_OIDC_CLIENTS.get("oauth2-proxy")
_oidc_secret = bw.kanidm_oidc_secret(_oidc_client["secret_field"]) if _oidc_client else ""

# --- Binary ---

server.shell(
    name=f"Install oauth2-proxy {VERSION}",
    commands=[
        f"""
        STAMP=/usr/local/bin/.oauth2-proxy-version
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{VERSION}" ]; then
          curl -fsSL "{BINARY_URL}" | tar -xz --strip-components=1 \
            -C /usr/local/bin "oauth2-proxy-{VERSION}.linux-arm64/oauth2-proxy"
          chmod +x /usr/local/bin/oauth2-proxy
          echo '{VERSION}' > "$STAMP"
        fi
        """,
    ],
)

# --- Config ---

files.directory(
    name="Create /etc/oauth2-proxy",
    path="/etc/oauth2-proxy",
    user="root",
    group="root",
    mode="755",
    present=True,
)

config_cfg = f"""\
provider = "oidc"
oidc_issuer_url = "https://idm.{DOMAIN}/oauth2/openid/oauth2-proxy"
client_id = "oauth2-proxy"
# client_secret + cookie_secret come from /etc/secrets/oauth2-proxy.env

http_address = "{OAUTH2_PROXY["host"]}:{OAUTH2_PROXY["port"]}"
reverse_proxy = true

cookie_domains = [".{DOMAIN}"]
cookie_secure = true
cookie_httponly = true
cookie_samesite = "lax"
whitelist_domains = [".{DOMAIN}"]
session_store_type = "cookie"

redirect_url = "https://auth.{DOMAIN}/oauth2/callback"
upstreams = ["static://202"]

# Kanidm enforces PKCE by default for OAuth2 clients.
code_challenge_method = "S256"

# Use Kanidm's `preferred_username` (e.g. "eetu") for X-Auth-Request-User,
# so trusted-header auth on downstream services (Navidrome) matches the
# native username rather than the email.
user_id_claim = "preferred_username"

# Allow concurrent OAuth flows (browser prefetcher, multiple tabs) without
# CSRF cookie collisions causing token-exchange to fail.
cookie_csrf_per_request = true

email_domains = ["*"]
skip_provider_button = true
set_xauthrequest = true

# Don't force the Kanidm consent screen on every login (default is "force").
prompt = "none"
"""

files.put(
    name="Write oauth2-proxy config",
    src=io.BytesIO(config_cfg.encode()),
    dest="/etc/oauth2-proxy/oauth2-proxy.cfg",
    user="root",
    group="root",
    mode="644",
)

# --- systemd service ---

service_unit = """\
[Unit]
Description=oauth2-proxy forward-auth gateway
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=/etc/secrets/oauth2-proxy.env
ExecStart=/usr/local/bin/oauth2-proxy --config=/etc/oauth2-proxy/oauth2-proxy.cfg
Restart=always
RestartSec=5
NoNewPrivileges=true
MemoryMax=64M
ProtectSystem=strict
ProtectHome=yes
PrivateTmp=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictNamespaces=yes
LockPersonality=yes
CapabilityBoundingSet=

[Install]
WantedBy=multi-user.target
"""

files.put(
    name="Write oauth2-proxy systemd unit",
    src=io.BytesIO(service_unit.encode()),
    dest="/etc/systemd/system/oauth2-proxy.service",
    user="root",
    group="root",
    mode="644",
)

# --- Start only once the OIDC client secret exists in BW ---
# secrets.py also gates on _oidc_secret, so the env file won't exist before then.

if _oidc_secret:
    _hash = hashlib.sha256((config_cfg + service_unit).encode()).hexdigest()

    systemd.service(
        name="Enable oauth2-proxy",
        service="oauth2-proxy",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart oauth2-proxy if config or env changed",
        commands=[
            restart_if_changed("oauth2-proxy", _hash, env_files=("/etc/secrets/oauth2-proxy.env",))
        ],
    )
