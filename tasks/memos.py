"""Memos: lightweight self-hosted memo / note service (Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

import vault as bw
from group_data.all import KANIDM_OIDC_CLIENTS, MEMOS, NETWORK
from tasks.util import resolve_latest

DOMAIN = NETWORK["domain"]

_image = (
    resolve_latest("usememos/memos", MEMOS["image"])
    if MEMOS.get("resolve_latest")
    else MEMOS["image"]
)

_oidc_client = KANIDM_OIDC_CLIENTS.get("memos")
_oidc_secret = bw.kanidm_oidc_secret(_oidc_client["secret_field"]) if _oidc_client else ""

quadlet = f"""\
[Unit]
Description=Memos
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=memos
Image={_image}
Network=host
Volume=/var/lib/memos:/var/opt/memos
Environment=TZ=Europe/Helsinki
Environment=MEMOS_PORT={MEMOS["port"]}
Environment=MEMOS_ADDR={MEMOS["host"]}
Environment=MEMOS_MODE=prod
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax={MEMOS["memory_max"]}

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

files.directory(
    name="Create memos data dir",
    path="/var/lib/memos",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write memos.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/memos.container",
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
    name="Start Memos",
    service="memos",
    running=True,
    daemon_reload=True,
)

server.shell(
    name="Restart Memos if quadlet changed",
    commands=[
        f"""
        STAMP=/etc/containers/systemd/.memos-quadlet-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart memos
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)

# Bootstrap the admin user from Bitwarden. The first account created via
# POST /api/v1/users gets the ADMIN role; subsequent calls return 4xx once a
# user with that username exists, so this is idempotent across re-deploys.
server.shell(
    name="Bootstrap Memos admin user",
    commands=[
        f"""
        set -eu
        MEMOS_URL="http://{MEMOS["host"]}:{MEMOS["port"]}"
        for i in $(seq 1 30); do
          STATUS=$(curl -s -o /dev/null -w '%{{http_code}}' "$MEMOS_URL/healthz" 2>/dev/null || true)
          if [ "$STATUS" = "200" ]; then break; fi
          sleep 2
        done
        set -a
        . /etc/secrets/memos.env
        set +a
        # Memos expects a flat {{username, password}} body (the docs' nested
        # `{{user:{{...}}}}` shape is rejected as "invalid username").
        BODY=$(jq -nc --arg u "$MEMOS_USERNAME" --arg p "$MEMOS_PASSWORD" \\
          '{{username:$u, password:$p}}')
        printf '%s' "$BODY" | curl -sS -o /dev/null -w '[memos] bootstrap user: HTTP %{{http_code}}\\n' \\
          -X POST "$MEMOS_URL/api/v1/users" \\
          -H 'Content-Type: application/json' --data-binary @- || true
        """,
    ],
)

# Register Kanidm as the Memos OAuth2 identity provider via REST. Skipped on
# the very first deploy when kanidm_oidc.py hasn't yet generated the client
# secret — the next deploy picks it up.
if _oidc_client and _oidc_secret:
    server.shell(
        name="Register Kanidm as Memos OAuth2 IdP",
        commands=[
            f"""
            set -eu
            MEMOS_URL="http://{MEMOS["host"]}:{MEMOS["port"]}"
            set -a
            . /etc/secrets/memos.env
            set +a

            if [ -z "${{MEMOS_OIDC_CLIENT_SECRET:-}}" ]; then
              echo "[memos] no client secret in memos.env yet — skipping IdP registration"
              exit 0
            fi

            # Memos returns the access token in the JSON body (Set-Cookie uses
            # a non-standard `Grpc-Metadata-` prefix that curl's cookie jar
            # ignores) so capture it and pass via Bearer for subsequent calls.
            LOGIN_BODY=$(jq -nc --arg u "$MEMOS_USERNAME" --arg p "$MEMOS_PASSWORD" \\
              '{{passwordCredentials:{{username:$u, password:$p}}}}')
            TOKEN=""
            for i in $(seq 1 10); do
              RESP=$(printf '%s' "$LOGIN_BODY" | curl -sS -X POST "$MEMOS_URL/api/v1/auth/signin" \\
                -H 'Content-Type: application/json' --data-binary @- || true)
              TOKEN=$(echo "$RESP" | jq -r '.accessToken // empty' 2>/dev/null || true)
              if [ -n "$TOKEN" ]; then break; fi
              sleep 2
            done
            if [ -z "$TOKEN" ]; then
              echo "[memos] login failed — skipping IdP registration" >&2
              exit 0
            fi

            EXISTING=$(curl -sS -H "Authorization: Bearer $TOKEN" "$MEMOS_URL/api/v1/identity-providers" \\
              | jq -r '.identityProviders[]?.title' 2>/dev/null || true)
            if echo "$EXISTING" | grep -qx 'Kanidm'; then
              echo "[memos] Kanidm IdP already registered — skipping"
              exit 0
            fi

            # Memos expects a flat body — wrapping in `identityProvider` creates
            # an empty record (title/type/config silently dropped).
            IDP_BODY=$(jq -nc --arg cs "$MEMOS_OIDC_CLIENT_SECRET" '{{
              type: "OAUTH2",
              title: "Kanidm",
              config: {{
                oauth2Config: {{
                  clientId: "memos",
                  clientSecret: $cs,
                  authUrl: "https://idm.{DOMAIN}/ui/oauth2",
                  tokenUrl: "https://idm.{DOMAIN}/oauth2/token",
                  userInfoUrl: "https://idm.{DOMAIN}/oauth2/openid/memos/userinfo",
                  scopes: ["openid", "email", "profile"],
                  fieldMapping: {{
                    identifier: "email",
                    displayName: "name",
                    email: "email"
                  }}
                }}
              }}
            }}')

            printf '%s' "$IDP_BODY" | curl -sS -o /dev/null \\
              -w '[memos] register Kanidm IdP: HTTP %{{http_code}}\\n' \\
              -H "Authorization: Bearer $TOKEN" \\
              -X POST "$MEMOS_URL/api/v1/identity-providers" \\
              -H 'Content-Type: application/json' --data-binary @-
            """,
        ],
    )
