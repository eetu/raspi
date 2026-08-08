# Copy this file to all.py and fill in your values.
# all.py is gitignored — never commit it.

NETWORK = {
    "lan_cidr": "192.168.x.0/24",  # your LAN subnet
    "lan_ip": "192.168.x.y",  # static IP reserved for the Pi
    "router": "192.168.x.1",  # your router
    "router_user": "your-router-username",  # SSH user on the router
    "router_ssh_port": 22,  # SSH port on the router
    "domain": "yourdomain.com",  # domain managed in Cloudflare
}

# NetBird — self-hosted zero-trust overlay, the only route in from outside the
# LAN. Required tier: a missing dict should fail the deploy loudly rather than
# ship a Pi with no remote access.
#
# `netbird` is deliberately absent from _SUBDOMAIN_NAMES at the bottom of this
# file. Every name there gets a Cloudflare A record pointing at the *LAN* IP; the
# coordinator needs a genuinely WAN-pointing A + AAAA, which tasks/ddns.py owns
# and refreshes every 5 minutes. The Pi-hole split-DNS override that keeps LAN
# clients off the router's hairpin comes from the `netbird` entry in
# KANIDM_OIDC_CLIENTS instead (public_dns: False → Pi-hole only).
NETBIRD = {
    "url_prefix": "netbird",
    # Traefik upstreams. See tasks/traefik.py: three routers, because the gRPC
    # paths need an h2c backend and the dashboard is a priority-1 catch-all.
    #
    # `host` is what Traefik dials, not what netbird-server binds. The server runs
    # with Network=host and derives its bind from the *port* of listenAddress only
    # (combined/cmd/root.go throws the host part away), so it binds 0.0.0.0:8081
    # and ufw's default-deny is what keeps it off the LAN. The dashboard has no
    # such limit — it runs in a bridge net and really does publish on loopback.
    "host": "127.0.0.1",
    "port": 8081,  # netbird-server: REST + gRPC + relay WebSocket + embedded IdP
    "dashboard_port": 8082,  # netbirdio/dashboard nginx
    # STUN is UDP and cannot be proxied — it is reachable directly on the host and
    # forwarded by the router. Everything else rides 443 through Traefik.
    "stun_port": 3478,
    # Both also bind on the host (Network=host), so they must not collide with
    # anything in the CLAUDE.md ports table — 9090 in particular is oauth2-proxy.
    "health_port": 9092,
    "metrics_port": 9091,
    "log_level": "info",
    # Hide the embedded IdP's email/password login so Kanidm is the only way in.
    # This is a SERVER-config switch (server.auth.localAuthDisabled), not an
    # account setting: the account API exposes a `local_auth_disabled` field but it
    # is a read-only mirror — PUTting it is silently ignored, because
    # IsLocalAuthDisabled() reads the IdP manager, not account settings.
    #
    # REBUILD CAVEAT: /api/setup creates a *local* owner to hand out the first API
    # token, and the same flag gates the embedded-IdP user-creation paths
    # (createEmbeddedIdpUser, CreateUserInvite, AcceptUserInvite). If a bootstrap
    # on a blank store ever fails with "local user creation is disabled", set this
    # to False for that one deploy, let the bootstrap complete, then set it back.
    "local_auth_disabled": True,
    # Server and dashboard releases are coupled — bump together. `resolve_latest`
    # is deliberately absent: this is core infra on a 1 GB Pi, and a surprise
    # minor is how you lose remote access (cf. kanidm 1.10.3 SIGILL on the Pi 4).
    "server_image": "docker.io/netbirdio/netbird-server:0.76.1",
    "dashboard_image": "docker.io/netbirdio/dashboard:v2.90.9",
    # The netbird CLIENT on this host, enrolled as the routing peer that carries
    # LAN traffic for everyone else. Keep == server_image tag; tasks/netbird_agent.py
    # installs/upgrades/downgrades the apt package on drift.
    "agent_version": "0.76.1",
    "agent_hostname": "raspi",  # referenced by `peer_hostname` in routes below
    # UDP port the agent's kernel WireGuard interface listens on. NetBird needs no
    # inbound port to work — peers fall back to the relay — but forwarding this one
    # to the Pi lets peers hole-punch straight to it, which turns the data path from
    # a userspace WSS relay hop (on this very Pi) into kernel WireGuard. Worth it on
    # a 1 GB host carrying music/syncthing/audiobook traffic over the mesh.
    #
    # Declared here rather than left to the client default so the ufw rule
    # (tasks/hardening.py) and the `netbird up --wireguard-port` flag cannot drift.
    # Set to None to keep the port closed and stay relay-only.
    "agent_wireguard_port": 51820,
    # Server paths are `-server` suffixed on purpose: the netbird *agent* owns
    # /var/lib/netbird (its profile and peer identity — active_profile.json,
    # default.json, state.json) and /var/log/netbird, so the coordinator must not
    # share either directory with it.
    "install_dir": "/etc/netbird-server",
    "data_dir": "/var/lib/netbird-server",
    # One-time POST /api/setup owner. The password auto-generates into the vault;
    # once Kanidm federation is live this is only a break-glass local login.
    #
    # THE DOMAIN OF THIS ADDRESS IS LOAD-BEARING. NetBird derives the account's
    # domain from its owner and auto-joins only same-domain sign-ins, so a
    # federated user whose email domain differs gets their own separate account
    # rather than joining this one — which silently splits the mesh in half (the
    # coordinator's peers in one account, the human's in another). Keep this on the
    # same domain as the people in KANIDM_PERSONS.
    "bootstrap_owner_email": "netbird-bootstrap@yourdomain.com",
    "bootstrap_owner_name": "Bootstrap Owner",
    # Emails promoted to NetBird admin on every reconcile. The embedded IdP
    # re-issues JWTs and drops upstream role claims, so admin cannot be
    # JWT-driven — this is the supported workaround. Must match a Kanidm person's
    # `email` in KANIDM_PERSONS, and they must have signed in at least once.
    "admin_emails": ["you@yourdomain.com"],
    # Account-level settings, PUT on every reconcile (declared keys merged over
    # whatever NetBird already has). The two mesh ranges are read at plan time by
    # tasks/hardening.py and tasks/network_restrict.py, so they must be declared
    # here rather than left to NetBird's random default.
    "account_settings": {
        "dns_domain": "mesh.yourdomain.com",
        "network_range": "100.92.0.0/16",
        # ULA /64 for the dual-stack overlay (NetBird v0.71+). Distinct from the
        # retired WireGuard fd00::/64 so stale routes can't shadow it.
        "network_range_v6": "fd0e:7b1d:4a2c::/64",
        # NB: only keys netbird-server actually returns in /accounts settings
        # belong here. A key it does not echo back can never compare equal, so it
        # would re-PUT on every single deploy — `user_approval_required` and
        # `peer_approval_enabled` did exactly that before being removed (they are
        # not part of the v0.76.1 settings schema). The reconcile step warns about
        # unrecognised keys so the next typo is visible instead of silent.
        # Keep False while idm.{domain} is allowlisted. Enabling it forces every
        # SSO-enrolled peer to periodically re-authenticate in a browser against
        # Kanidm, which an off-LAN peer cannot reach — it would simply strand.
        # See "Enrolling peers" in CLAUDE.md.
        "peer_login_expiration_enabled": False,
        # Lets the routing peer answer DNS for the networks it carries.
        "routing_peer_dns_resolution_enabled": True,
        "jwt_groups_enabled": False,
        "jwt_allow_groups": [],
    },
    # Groups to ensure exist. NetBird auto-creates `All` (every peer).
    "groups": ["admins"],
    # Peer-enrolment keys. The plaintext key is returned only by the POST, so the
    # reconcile step captures it into the vault as `setup_key_<name>`;
    # tasks/secrets.py then drops the `devices` one at
    # /etc/secrets/netbird-setup-key for this host's own agent to enrol with.
    # NetBird caps expires_in at one year (and requires at least a day), so there
    # is no "never" — the reconcile step replaces a key once it is inside
    # `renew_within_days` of expiry, the same way it rotates the API token.
    "setup_keys": [
        {
            "name": "devices",
            "type": "reusable",
            "expires_in": 31536000,  # seconds; 365 days is the API maximum
            "usage_limit": 0,  # 0 = unlimited
            "auto_groups": [],
            "ephemeral": False,
        },
    ],
    # Which of the keys above this host enrols with. It is the only one
    # tasks/secrets.py drops on disk; the rest stay in the vault to be pasted into
    # phones and laptops.
    "agent_setup_key": "devices",
    # How close to expiry a setup key or the API token may get before the
    # reconcile step mints a replacement. Deploy at least this often and the
    # credentials never lapse without anyone touching a dashboard.
    "renew_within_days": 30,
    # Network routes carried by the `raspi` peer. This is what replaces the old
    # wg0 AllowedIPs: mesh clients reach LAN hosts (and every service behind
    # Traefik) through the Pi.
    "routes": [
        {
            "network_id": "home-lan",
            "description": "Home LAN via the Pi, so mesh clients reach every LAN host and the Traefik vhosts.",
            "network": "192.168.x.0/24",  # keep == NETWORK["lan_cidr"]
            "peer_hostname": "raspi",
            "groups": ["All"],
            "enabled": True,
            "masquerade": True,
            "metric": 9999,
        },
        # Opt-in full tunnel. skip_auto_apply advertises the route to everyone
        # without applying it, so the default stays split-tunnel and a client
        # only routes everything through home after selecting it (CLI:
        # `netbird routes select secure-all-traffic`). NetBird auto-creates the
        # matching ::/0 route for IPv6-capable peers.
        {
            "network_id": "secure-all-traffic",
            "description": "Route ALL traffic through home — turn on for untrusted networks (cafe / hotel Wi-Fi). Opt-in; default is split tunnel.",
            "network": "0.0.0.0/0",
            "peer_hostname": "raspi",
            "groups": ["All"],
            "enabled": True,
            "masquerade": True,
            "metric": 9999,
            "skip_auto_apply": True,
        },
    ],
    # Mesh DNS: send {domain} queries to Pi-hole so `memo.{domain}` and friends
    # resolve to their LAN IPs for connected peers, exactly as on the LAN. The
    # nameserver IP is the Pi's *mesh* address, which NetBird assigns — the
    # reconcile step resolves it from /api/peers rather than pinning it, so it
    # self-heals if the peer is ever deleted and re-enrolled.
    "nameservers": [
        {
            "name": "home-dns",
            "description": "Pi-hole for the home domain (split DNS over the mesh).",
            "peer_hostname": "raspi",  # resolved to the peer's mesh IP
            "port": 53,
            "groups": ["All"],
            "domains": ["yourdomain.com"],
            "enabled": True,
            "primary": False,
            "search_domains_enabled": False,
        },
    ],
}

# Inbound email DNS — apex MX/SPF/DKIM/DMARC + provider domain verification.
# Records are written by tasks/cloudflare_dns.py. Provider-agnostic shape so
# Proton/Fastmail/Migadu/etc. can be swapped by editing values, not code.
# Comment the dict to skip all email DNS wiring.
#
# Two-deploy bootstrap (Proton):
#   1. Fill verification_txt + mx + spf + dmarc, deploy. Wait until Proton
#      dashboard turns each record green.
#   2. Paste the 3 DKIM CNAME targets shown in Proton into `dkim`, redeploy.
EMAIL = {
    "provider": "proton",  # informational; record values come from below
    # Provider domain-ownership TXT at apex (e.g. "protonmail-verification=...").
    # Co-exists with SPF — both are TXT at @ but matched on exact content.
    "verification_txt": "protonmail-verification=<token>",
    # Apex MX. Lower priority = higher preference.
    "mx": [
        ("mail.protonmail.ch", 10),
        ("mailsec.protonmail.ch", 20),
    ],
    # Apex SPF TXT.
    "spf": "v=spf1 include:_spf.protonmail.ch ~all",
    # DKIM CNAMEs — keep empty on deploy 1, fill after Proton verifies domain.
    "dkim": {
        # "protonmail._domainkey":  "protonmail.domainkey.<id>.domains.proton.ch",
        # "protonmail2._domainkey": "protonmail2.domainkey.<id>.domains.proton.ch",
        # "protonmail3._domainkey": "protonmail3.domainkey.<id>.domains.proton.ch",
    },
    # DMARC TXT at `_dmarc`. Start `p=quarantine`; tighten to `p=reject` after
    # ~2 weeks of clean aggregate reports. The rua/ruf mailbox must exist as
    # an address/alias on the domain before reports arrive.
    "dmarc": "v=DMARC1; p=quarantine; rua=mailto:postmaster@yourdomain.com; ruf=mailto:postmaster@yourdomain.com; fo=1",
}

# Off-Pi LLM endpoint (Mac mini ../mini repo). Traefik proxies ai.{domain}
# to this LAN address; the Mini owns auth (currently none — bare proxy).
AI = {
    "host": "192.168.x.y",  # Mac mini LAN IP
    "port": 11434,  # Caddy gateway port on the Mini
    "url_prefix": "ai",
    "public_dns": True,
}

# Off-Pi image-generation endpoint (Mac mini ../mini repo, ComfyUI w/ Flux
# Kontext img2img). Traefik proxies comfy.{domain} to the Mini's Caddy on
# port 8188; the Mini owns auth (toggle COMFYUI["require_api_key"] in the
# ../mini repo — strongly recommended ON when exposing publicly, since
# ComfyUI has no native auth and a single workflow submission pegs the
# Mac's GPU for ~30-50 s). ComfyUI uses a WebSocket at /ws for progress
# events; Traefik passes WS upgrades through automatically.
COMFY = {
    "host": "192.168.x.y",  # Mac mini LAN IP (same as AI)
    "port": 8188,  # Caddy gateway port for ComfyUI on the Mini
    "url_prefix": "comfy",
    "public_dns": True,
}

# Off-Pi speech-to-text endpoint (Mac mini ../mini repo, whisper.cpp HTTP
# server). Traefik proxies stt.{domain} to the Mini's Caddy on port 8190;
# the Mini owns auth (toggle WHISPER["require_api_key"] in the ../mini repo).
STT = {
    "host": "192.168.x.y",  # Mac mini LAN IP (same as AI)
    "port": 8190,  # Caddy gateway port for Whisper on the Mini
    "url_prefix": "stt",
    "public_dns": True,
}

# Off-Pi text-to-speech endpoint (Mac mini ../mini repo, Piper TTS). Traefik
# proxies tts.{domain} to the Mini's Caddy on port 8192; the Mini owns auth
# (toggle PIPER["require_api_key"] in the ../mini repo).
TTS = {
    "host": "192.168.x.y",  # Mac mini LAN IP (same as AI)
    "port": 8192,  # Caddy gateway port for Piper on the Mini
    "url_prefix": "tts",
    "public_dns": True,
}

UNBOUND = {
    "port": 5335,
    "msg_cache_mb": 50,  # message cache — increase to 100 if you have RAM to spare
    "rrset_cache_mb": 100,  # RRset cache should be ~2x msg_cache
}

PIHOLE = {
    "host": "127.0.0.1",
    "web_port": 8080,  # moved off 80 so Traefik owns it
    "url_prefix": "pihole",
    "public_dns": True,
    "history_days": 7,  # query log retention; default is 365
    # Pin to a specific Pi-hole release tag. Installer URL is constructed from this tag so the
    # SHA-256 is stable. To upgrade: bump version, then update installer_sha256 with:
    #   python3 -c "import urllib.request, hashlib; v='v6.x.y'; \
    #     print(hashlib.sha256(urllib.request.urlopen(
    #       f'https://raw.githubusercontent.com/pi-hole/pi-hole/{v}/automated%20install/basic-install.sh'
    #     ).read()).hexdigest())"
    "version": "v6.4.1",
    "installer_sha256": "a86c23c0c0911496585e9e73ec6d5fc2a60b68b135d9ba678569d9476d676e16",
    "blocklists": [
        "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.txt",
        "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.medium.txt",
        "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/popupads.txt",
    ],
}

CHAT = {
    "host": "127.0.0.1",
    "port": 3002,
    "image": "ghcr.io/eetu/chat:main",
    # Plain config env vars. See chat/backend/.env.example.
    "env": {
        "CHAT_TTL_DAYS": "30",
    },
}

# Represent — markdown demo-script presenter (../represent). Subdomain +
# public_dns flag live on its KANIDM_OIDC_CLIENTS entry (the chat/scribe pattern).
REPRESENT = {
    "host": "127.0.0.1",
    "port": 3008,
    "image": "ghcr.io/eetu/represent:main",
    "env": {},
}

# Nib — direct-manipulation SVG path editor (../nib). Rust core + SvelteKit SPA in one
# image; projects live in a SQLite file under /var/lib/nib. Subdomain + public_dns flag
# live on its KANIDM_OIDC_CLIENTS entry (the represent pattern).
#
# Also serves an MCP tool surface at /mcp so an LLM can co-edit a document live alongside
# the browser. That path authenticates with each user's own bearer token (shown in nib's
# Settings), NOT SSO — which is why nib is deliberately absent from traefik's _gated_hosts:
# it runs its own OIDC client, and oauth2-proxy at the edge would 401 an MCP client, which
# sends a bearer and has no session cookie.
NIB = {
    "host": "127.0.0.1",
    "port": 3009,
    "image": "ghcr.io/eetu/nib:main",
    "env": {},
}

# Scribe — self-hosted Audible library mirror. Talks to shim over loopback,
# ships ffmpeg work to scribe-press on the mini. The library/ tree on the
# CIFS audiobooks share is what shelf serves to clients; original/ is the
# cold-storage AAXC tree (never exposed).
SCRIBE = {
    "host": "127.0.0.1",
    "port": 3003,
    "url_prefix": "scribe",
    "image": "ghcr.io/eetu/scribe:main",
    "env": {
        # Press worker on the mini — set when mini IaC has been deployed.
        # Bearer goes in `secret_env` below.
        # "SCRIBE_PRESS_URL": "https://scribe-press.<mini-host>:3005",
        "SCRIBE_AUTO_ENQUEUE": "1",
        "SCRIBE_POLL_INTERVAL_MIN": "60",
        # Reconvert needs press to reach scribe directly over the LAN
        # to fetch /internal/aaxc/{token}. Loopback bind defeats that —
        # override here. Traefik still dials 127.0.0.1, which 0.0.0.0
        # naturally includes, so public routing is unaffected.
        # "SCRIBE_BIND": "0.0.0.0:3003",
        # LAN URL of this scribe instance, as seen from the mini-side
        # press worker. Used during reconvert: scribe mints a one-shot
        # /internal/aaxc/{token} URL so press can pull the locally-
        # stored AAXC without backend → mini file shipping. Unset =
        # reconvert disabled, normal downloads unaffected.
        # "SCRIBE_INTERNAL_URL": "http://<raspi-lan-ip>:3003",
        # SCRIBE_SHELF_URL is auto-derived in tasks/scribe.py from
        # SHELF["url_prefix"] + NETWORK["domain"] (https form) so scribe's
        # UI shows the public URL for copy/paste into ABS clients. Set
        # an override here only if you want something other than the
        # public Traefik route.
        "SCRIBE_OPEN_REGISTRATION": "0",
        "SCRIBE_ADMIN_EMAIL": "",
    },
    "secret_env": {
        # Bearer for the scribe → scribe-press hop. Same value lives in the
        # mini's `mini/scribe-press` BW item under `api_key`. Paste it into
        # raspi's `scribe` BW item under `press_token`.
        "SCRIBE_PRESS_TOKEN": "press_token",
        "ABS_TOKEN": "abs_token",
        # Shelf bearer — scribe surfaces it on /api/me so logged-in
        # users can copy/paste into Listen This. Same value also lands
        # in /etc/secrets/shelf.env so both services agree.
        "SCRIBE_SHELF_API_KEY": "shelf_api_key",
    },
}

# Shim — Audible auth + library + voucher sidecar (loopback-only).
SHIM = {
    "host": "127.0.0.1",
    "port": 3004,
    "image": "ghcr.io/eetu/scribe-shim:main",
    "env": {},
}

# Shelf — optional read-only ABS-compatible sidecar over scribe's DB.
# Listen This and other ABS clients connect here directly (no real
# audiobookshelf required). Mounts scribe.db and the library tree
# read-only — no writable surface. Drop this dict or comment the
# `local.include("tasks/shelf.py")` line in deploy.py to disable.
SHELF = {
    "host": "127.0.0.1",
    "port": 3006,
    "url_prefix": "shelf",
    "public_dns": True,  # external clients (iOS app) want this reachable
    "image": "ghcr.io/eetu/scribe-shelf:main",
    "env": {
        "SHELF_LIBRARY_NAME": "Audiobooks",
    },
    # SHELF_API_KEY (bearer) lives in BW item `shelf` under field
    # `api_key`. tasks/secrets.py writes it to /etc/secrets/shelf.env.
    "secret_env": {
        "SHELF_API_KEY": "api_key",
    },
}

HALO = {
    "host": "127.0.0.1",
    "port": 3000,
    "url_prefix": "halo",
    "public_dns": True,
    "aliases": ("hcc",),  # legacy fallback — keep until clients migrate
    "image": "ghcr.io/eetu/halo:main",
    # Plain config env vars. See halo/backend/src/settings.rs.
    # Non-string values (dicts, lists, numbers, bools) are compact-JSON-serialized
    # at deploy time — keep structured config readable here.
    "env": {
        "LANGUAGE": "fi",
        "TOMORROW_IO_BASE_URL": "https://api.tomorrow.io",
        "FMI_BASE_URL": "https://opendata.fmi.fi/wfs",
        "HUE_BRIDGE_ADDRESS": "",
        "HUE_ROOM_TYPES": {
            "inside": [],
            "inside_cold": [],
            "outside": [],
        },
        "HALO_HISTORY_RETENTION_DAYS": "0",
        "SOLIS_STATION_ID": "",
        "SOLIS_BASE_URL": "https://www.soliscloud.com:13333",
    },
    # Secrets sourced from Bitwarden item `halo`. Map: env var name -> BW field name.
    # Each entry must exist as a hidden field on the BW item before deploy.
    # tasks/secrets.py writes these to /etc/secrets/halo.env at deploy time.
    "secret_env": {
        "TOMORROW_IO_API_KEY": "tomorrow_io_api_key",
        "HUE_BRIDGE_USER": "hue_bridge_user",
        "SOLIS_KEY_ID": "solis_key_id",
        "SOLIS_KEY_SECRET": "solis_key_secret",
    },
}

# One-shot FMI PV forecast runner. Posts JSON to Halo /api/pv/forecast on a timer.
# Geographic coverage: Finland, Scandinavia, Baltic states.
FMI_PV_FORECAST = {
    "image": "ghcr.io/eetu/fmi-pv-forecast-runner:latest",
    "schedule": "0/3:00",  # systemd OnCalendar — every 3 hours
    # Runner env vars. See fmi-pv-forecast-runner README.
    "env": {
        "PV_LAT": "-75.0",  # Antarctica placeholder — replace with your site
        "PV_LON": "0.0",
        "PV_TILT": "25",  # panel tilt from horizontal (degrees)
        "PV_AZIMUTH": "180",  # panel azimuth (180 = south)
        "PV_KW": "5",  # nominal system power
    },
}

# Optional. A private updater (built locally, pushed to your LAN registry) that
# writes generic reserve-market profit rows straight into Halo's SQLite. Comment
# the block to drop the feed. Credentials live in the `reserve` vault item (a
# LOGIN: username=email, password; plus an `api_base_url` field), wired by
# vault.reserve_creds() — never here. Deployed by tasks/halo.py alongside Halo.
RESERVE = {
    "image": "registry.lan:5000/reserve-updater:latest",  # your LAN (zot) registry
    "schedule": "*-*-* 9..18/3:00:00",  # OnCalendar — daytime only (09,12,15,18)
    "jitter": "30min",  # RandomizedDelaySec — small spread; nobody checks at night
    "env": {
        "RESERVE_PROVIDER": "reserve",
        "RESERVE_DISPLAY_NAME": "Reservimarkkina",
        "RESERVE_STEPS": "day,month,year",
        "RESERVE_ACTIVATION_FLOOR": "2025-11-01",  # earliest data; never epoch
    },
}

TRAEFIK = {
    "host": "0.0.0.0",
    "version": "v3.6.12",
    # Subdomains exempt from the `internal-only` ipAllowList. Public 443 is
    # forwarded to Traefik so the NetBird coordinator is reachable, and Traefik
    # matches on Host header rather than source IP — so without the allowlist
    # every vhost on the box would answer the internet. The polarity is
    # deliberate: the middleware is attached by default and only these names opt
    # out, so a route added later is LAN-only until someone says otherwise.
    # Add "idm" here if you want remote *browser* SSO login (see tasks/traefik.py);
    # off-LAN peer enrolment uses a setup key and does not need it.
    "public_hosts": ("netbird",),
}

# Aliases written into /etc/hosts. Values may be either a literal IP (e.g.
# "192.168.1.50", resolved verbatim by tasks/bootstrap.py) or an mDNS hostname
# ending in `.local` (resolved on the Pi by tasks/host_discover.py at boot and
# every 5 minutes). Pick mDNS for devices whose DHCP lease drifts — avahi
# tracks the live IP for you. Containers that need to resolve these aliases
# mount /etc/hosts read-only.
HOSTS = {
    "zenwifi": "your-nas.local",  # mDNS form; or use a literal IP if you prefer
}

SHELL = "/usr/bin/fish"  # /usr/bin/zsh, usr/bin/bash

# Shares split across two shared NAS logins (the BT8 caps accounts at 5):
# `readonly` and `readwrite`, stored in the `cifs` vault item as
# readonly_username/readonly_password and readwrite_username/
# readwrite_password. Every share names its login via `creds`; pick
# `readwrite` only where a service actually writes.
CIFS = {
    "audiobooks": {
        "share": "//zenwifi/audiobooks",  # alias from HOSTS above
        "mountpoint": "/mnt/audiobooks",
        "vers": "2.0",
        "sec": "ntlmsspi",
        "creds": "readwrite",  # scribe presses new books into the tree
    },
    "music": {
        "share": "//zenwifi/music",
        "mountpoint": "/mnt/music",
        "vers": "2.0",
        "sec": "ntlmsspi",
        "creds": "readonly",
    },
    # One mount serves both consumers: tracker binds {mountpoint}/mods
    # read-write (renames modules in place), party binds the archive :ro
    # (PARTY_ROOT=/mnt/scene/parties). readwrite creds because of tracker.
    "scene": {
        "share": "//zenwifi/scene",
        "mountpoint": "/mnt/scene",
        "vers": "2.0",
        "sec": "ntlmsspi",
        "creds": "readwrite",
    },
    "movies": {
        "share": "//zenwifi/movies",
        "mountpoint": "/mnt/movies",
        "vers": "2.0",
        "sec": "ntlmsspi",
        "creds": "readonly",
    },
    # Used by tasks/restic.py for the encrypted backup repository.
    "backups": {
        "share": "//zenwifi/backups",
        "mountpoint": "/mnt/backups",
        "vers": "2.0",
        "sec": "ntlmsspi",
        "creds": "readwrite",
    },
}

NTFY = {
    "host": "127.0.0.1",
    "port": 8090,
    "url_prefix": "ntfy",
    "public_dns": True,  # external push sources (CI webhooks, alerts) need to reach it
    "image": "docker.io/binwiederhier/ntfy:v2",
    "topic": "raspi-alerts",  # topic used by system notifications (Trivy, version checks)
}

GATUS = {
    "host": "127.0.0.1",
    "port": 3001,
    "image": "ghcr.io/twin/gatus:v5.35.0",
    "resolve_latest": True,
    "memory_max": "64M",
}

TRIVY = {
    "version": "0.69.3",
    # Caps the CVE-scan spike so an image scan can't starve the Pi. The scan is
    # a oneshot — if it exceeds this it's OOM-killed without touching other
    # services. Kept here (not in raspi-dashboard's own 96M budget) because the
    # scan runs in trivy's unit, triggered out-of-band.
    "memory_max": "768M",
}

# Encrypted incremental backups of service state to the NAS via restic.
# The repo lives under {CIFS["backups"]["mountpoint"]}/raspi-restic.
# Set CIFS["backups"] (above) and create the `restic` Bitwarden item before
# enabling. Remove this dict to opt out — tasks/restic.py becomes a no-op.
RESTIC = {
    "version": "0.18.0",
    # Service state directories restored verbatim on a blank Pi. Add new
    # entries here when adding services that store persistent data.
    "paths": [
        "/var/lib/vaultwarden",
        "/var/lib/kanidm",
        "/var/lib/navidrome",
        "/var/lib/tracker",  # tracker.db (path index + libopenmpt metadata cache)
        "/var/lib/memos",
        "/var/lib/gatus",
        "/var/lib/yarr",
        "/var/lib/syncthing",
        # NetBird coordinator state: SQLite store (peers, keys, ACLs, routes),
        # the embedded IdP's user db, and the activity log. Losing it means every
        # peer has to re-enrol, so it is the highest-value path in this list.
        "/var/lib/netbird-server",
        # The Pi's own agent profile/identity. Tiny, and the deploy would re-enrol
        # from the setup key anyway — but restoring it avoids leaving an orphan peer
        # (and a stale mesh IP) behind on a rebuild.
        "/var/lib/netbird",
        "/var/lib/beszel",
        "/var/lib/chat",
        "/var/lib/represent",  # represent.db (profiles/projects/documents)
        "/var/lib/nib",  # nib.db (users + svg projects, native model + cached export)
        "/var/lib/scribe",
        "/var/lib/shim",
        "/var/lib/zot",  # private OCI registry blob store (manifests + layers)
        "/etc/pihole",  # gravity.db (blocklists) + custom.list (local DNS) + setupVars
        "/etc/traefik/acme.json",
    ],
    "retention": {"daily": 7, "weekly": 4, "monthly": 6},
    # systemd OnCalendar — daily 03:30 local with 15min jitter.
    "schedule": "*-*-* 03:30:00",
    # Weekly prune to actually reclaim space from forgotten snapshots — kept
    # off the daily timer because prune is RAM-hungry and locks the repo.
    # `prune_max_unused` caps work per run (e.g. "100M") so the Pi 4 1GB doesn't OOM.
    "prune_schedule": "Sun *-*-* 04:30:00",
    "prune_max_unused": "100M",
    # Paths excluded from snapshots — derived/regenerable state that would
    # otherwise bloat the repo and overflow tmpfs /tmp during restic packing.
    "excludes": [
        "/var/lib/navidrome/cache",
        "/var/lib/navidrome/artwork",
    ],
}

VAULTWARDEN = {
    "host": "127.0.0.1",
    "port": 8085,
    # No floating major tag; resolve_latest fetches the latest 1.x.x at deploy time.
    "image": "docker.io/vaultwarden/server:1.33.2",
    "resolve_latest": True,
}

YARR = {
    "host": "127.0.0.1",
    "port": 7070,
    "url_prefix": "rss",
    "public_dns": True,
    "version": "v2.6",
}

SYNCTHING = {
    "version": "v2.0.16",
    "host": "127.0.0.1",
    "port": 8384,
    "url_prefix": "syncthing",
    "public_dns": True,
    "user": "root",
}

NAVIDROME = {
    "host": "127.0.0.1",
    "port": 4533,
    "url_prefix": "music",
    "public_dns": True,
    "image": "docker.io/deluan/navidrome:0.61.1",
    "resolve_latest": False,
}

# FastTracker 2-style player for the NAS module collection (group/artist/song).
# LAN-only: no oauth2-proxy in front (see tasks/traefik.py — tracker is NOT in
# _gated_hosts), so the container runs with TRACKER_OPEN=1 to skip the
# forward-auth header assertion. Egress-restricted in tasks/network_restrict.py.
TRACKER = {
    "host": "127.0.0.1",
    "port": 3010,
    "url_prefix": "tracker",
    "image": "ghcr.io/eetu/scene-tracker:main",
}

# Demoscene archive player. LAN-only like tracker (PARTY_OPEN=1, not gated).
# Content on the `scene` NAS share; loopback transcoder sidecar. Add
# `"public_dns": True` to expose it on a public subdomain.
PARTY = {
    "host": "127.0.0.1",
    "port": 3020,
    "url_prefix": "party",
    "image": "ghcr.io/eetu/scene-party:main",
}

# Multiplayer dice roller (Rust+SPA, room codes). Public/un-gated (no login,
# not in _gated_hosts), stateless (in-memory rooms only — no volume/DB/restic).
# Network=host + DICE_BIND on loopback keeps the raw backend off the LAN.
# Add `"public_dns": True` to expose it on a public subdomain.
DICE = {
    "host": "127.0.0.1",
    "port": 3040,
    "url_prefix": "dice",
    "image": "ghcr.io/eetu/dice:main",
    "memory_max": "64M",
    "ttl_secs": 7200,
    "max_dice": 8,
    "max_rooms": 5000,
    "max_players": 16,
}

# Stateless ffmpeg sidecar for party — loopback only, not web-routed.
TRANSCODER = {
    "host": "127.0.0.1",
    "port": 3021,
    "image": "ghcr.io/eetu/scene-transcoder:main",
    "memory_max": "256M",
}

MEMOS = {
    "host": "127.0.0.1",
    "port": 5230,
    # `stable` is the upstream rolling tag; Diun + AutoUpdate=registry track digest changes.
    "image": "docker.io/neosmemo/memos:stable",
    "resolve_latest": False,
    "memory_max": "128M",
}

VUIO = {
    "host": "0.0.0.0",  # LAN-wide for DLNA/SSDP discovery
    "port": 8096,
    "version": "v0.0.22",
    "movies_path": "/mnt/movies",
}

BESZEL = {
    "host": "127.0.0.1",  # loopback address local consumers use (traefik, dashboard, gatus)
    # Address the hub binds to. 0.0.0.0 keeps loopback working for local
    # consumers AND lets off-host agents (e.g. a camera node's beszel-agent)
    # reach it over the LAN. The LAN port is opened by tasks/hardening.py ufw
    # only on hosts running the `monitoring` feature.
    "bind": "0.0.0.0",
    "port": 8091,  # hub web UI (8090 taken by ntfy)
    "version": "v0.18.7",
    "agent_image": "docker.io/henrygd/beszel-agent:0.18.7",  # Podman Quadlet
    # Declarative non-superuser accounts. tasks/beszel.py generates each password
    # once (stored on the `beszel` BW item as `user_pw_<email>`) and on every
    # deploy upserts the PocketBase user (role + verified) + assigns systems.
    # role ∈ {user, admin, readonly}; systems = "all" or a list of system names.
    # Exactly one entry sets token_fetch: True — the account tasks/beszel.py
    # authenticates as to pull the agent's universal token (role `user`; readonly
    # can't mint tokens). The PocketBase superuser bootstrap is separate (the
    # `beszel` BW item login) and must NOT appear here.
    "users": [
        {"email": "agent@example.com", "role": "user", "systems": "all", "token_fetch": True},
        {"email": "dashboard@example.com", "role": "readonly", "systems": "all"},
        {"email": "you@example.com", "role": "user", "systems": "all"},
    ],
}

# raspi-dashboard — stateless fan-in of gatus health + beszel metrics + trivy
# CVE status onto one LAN-only page, behind oauth2-proxy (public_dns: False).
RASPI_DASHBOARD = {
    "host": "127.0.0.1",
    "port": 3007,
    "url_prefix": "dashboard",
    "image": "ghcr.io/eetu/raspi-dashboard:main",
    "public_dns": False,
    "memory_max": "96M",
    # Which beszel user (from BESZEL["users"]) this app authenticates as.
    "beszel_user": "dashboard@example.com",
}

# supersaw — browser synth (static SPA served by nginx, all audio client-side).
# Stateless, no secrets; behind oauth2-proxy (public_dns: False). The port is baked
# into the image's nginx.conf — keep in sync with the supersaw repo.
SUPERSAW = {
    "host": "127.0.0.1",
    "port": 3013,
    "url_prefix": "supersaw",
    "image": "ghcr.io/eetu/supersaw:main",
    "public_dns": False,
    "memory_max": "48M",
}

# zot — self-hosted private OCI image registry (native Go binary, not a
# container). LAN-only with NO auth (public_dns: False, route not oauth2-gated):
# push from a dev box over the LAN with `podman push registry.{domain}/app:tag`.
# The wildcard cert is Let's Encrypt-trusted, so no insecure-registry config is
# needed client-side. Blob store lives on local SD ext4 (dedupe needs hardlinks,
# which CIFS lacks) and is backed up to the NAS via restic. No secrets.
# `keep_tags` drives the retention policy (most-recently-pushed tags kept/repo;
# untagged manifests GC'd). Belongs to the `apps` feature.
ZOT = {
    "host": "127.0.0.1",
    "port": 5000,
    "url_prefix": "registry",
    "version": "v2.1.17",
    "public_dns": False,
    "memory_max": "256M",
    "keep_tags": 10,
}

# ocular — camera-vision app on a separate camera node (e.g. a Pi 3 B+ with a
# camera). raspi only proxies it: Traefik upstream is the node's LAN IP (the
# AI/COMFY off-host pattern). The camera/detector block is rendered into
# /etc/ocular/config.json on the node by tasks/ocular.py (native deploy, shipped
# from the sibling ../ocular working tree). LAN-only subdomain (no "public_dns").
# Belongs to the `camera` feature.
OCULAR = {
    "host": "192.168.x.z",  # camera node LAN IP
    "port": 8099,
    "url_prefix": "ocular",
    # Release pulled onto the Pi: "main" (rolling prerelease, refreshed on every
    # push to main) or a pinned tag like "v1.2.0". Default "main".
    "version": "main",
    # rotation: 0 for an upright mount; 90/270 sideways; 180 upside-down. Live-
    # tunable from the UI, so just confirm from the feed.
    "camera": {"width": 640, "height": 480, "fps": 15, "rotation": 0},
    "revolution": {
        "roi": [280, 200, 80, 80],  # marker region in processing px — tune live
        "threshold": 60,
        "debounce_frames": 3,
        "wheel_circumference_m": 0.0,  # set once measured → enables distance
        "marker_is_dark": True,
    },
}

MCP_CHAT = {
    "host": "127.0.0.1",
    "port": 8092,  # `:main` floats — Pull=newer + AutoUpdate=registry track ghcr.
    "url_prefix": "mcp-chat",
    "image": "ghcr.io/eetu/chat-mcp:main",
    # Public DNS A record points to LAN IP — name resolves anywhere but only
    # LAN/VPN clients can reach it. Lets roaming machines (cellular hotspot)
    # resolve via their default resolver instead of needing Pi-hole/WG.
    "public_dns": True,
    # MCP bridge for chat's img2img + inpaint endpoints. Speaks streamable-HTTP
    # MCP at `/mcp`. CHAT_MCP_API_KEY (backend) and CHAT_MCP_SERVER_KEY (this
    # service) are both opt-in — leave unset while we trust the LAN perimeter.
}

KANIDM = {
    "host": "127.0.0.1",
    "port": 8443,
    "url_prefix": "idm",
    "public_dns": True,
    # Pin to a specific release. Do NOT use resolve_latest on the Pi 4:
    # kanidm 1.10.3 SIGILLs (exit 132) on the Cortex-A72 — that build needs a
    # newer CPU baseline (kanidm#4371, fix in PR #4372). 1.10.2 is the newest
    # confirmed working; 1.9.2 is the long-standing known-good.
    "image": "docker.io/kanidm/server:1.9.2",
    "resolve_latest": False,
    # Login-session lifetime (seconds) for home users, set as account policy on
    # idm_all_persons. Bounds the OAuth2 refresh-token lifetime too (access
    # tokens stay short ~15 min; refresh rides the session). Default kanidm
    # session is only 8 h. 2592000 = 30 days; raise for a trusted LAN-only IdP.
    "auth_session_expiry": 2592000,
}

OAUTH2_PROXY = {
    "host": "127.0.0.1",
    "port": 9090,
    "version": "v7.15.2",
}

# One entry per service that authenticates via Kanidm OIDC.
# secret_field: name of the hidden field in the `kanidm` Bitwarden item.
# disable_pkce: set True for clients that don't support PKCE (Kanidm enforces it by default).
KANIDM_OIDC_CLIENTS = {
    "vaultwarden": {
        "display_name": "Vaultwarden Password Manager",
        "url_prefix": "vault",  # → https://vault.{domain}
        "public_dns": True,  # mobile Bitwarden client needs reachable URL on cellular
        "redirect_path": "/identity/connect/oidc-signin",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "vw_client_secret",
    },
    "gatus": {
        "display_name": "Gatus Monitoring",
        "url_prefix": "gatus",
        "public_dns": True,
        "redirect_path": "/authorization-code/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "gatus_client_secret",
        "disable_pkce": True,
    },
    # NetBird federates this client into its own embedded IdP (Dex) rather than
    # consuming it directly, which makes the redirect URI unusual in two ways:
    #
    #  - `public_dns: False` even though netbird.{domain} IS in public DNS. This
    #    flag only controls whether tasks/cloudflare_dns.py writes a LAN-IP A
    #    record; the coordinator's public A + AAAA are WAN-pointing and owned by
    #    tasks/ddns.py. False here means "Pi-hole split-DNS override only", which
    #    is exactly what is wanted — see the NETBIRD dict for the full story.
    "netbird": {
        "display_name": "NetBird",
        "url_prefix": "netbird",
        "public_dns": False,
        # Dex uses this bare callback, NOT the /oauth2/callback/<connector-id>
        # form the NetBird docs describe — confirmed by reading the redirect_uri
        # off a live authorize request. An earlier version of this file carried
        # machinery to feed the suffixed URL back from the vault; it was never
        # used, so it is gone. If a future NetBird switches to the suffixed form,
        # login fails with an invalid-redirect error and this is the place to fix.
        "redirect_path": "/oauth2/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "netbird_client_secret",
    },
    "beszel": {
        "display_name": "Beszel Monitoring",
        "url_prefix": "beszel",
        "public_dns": True,
        "redirect_path": "/api/oauth2-redirect",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "beszel_client_secret",
    },
    "oauth2-proxy": {
        "display_name": "OAuth2 Proxy Forward-Auth Gateway",
        "url_prefix": "auth",
        "public_dns": True,
        "redirect_path": "/oauth2/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "oauth2_proxy_client_secret",
    },
    "memos": {
        "display_name": "Memos",
        "url_prefix": "memo",
        "public_dns": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "memos_client_secret",
    },
    "chat": {
        "display_name": "Chat",
        "url_prefix": "chat",
        "public_dns": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "chat_client_secret",
    },
    "scribe": {
        "display_name": "Scribe",
        "url_prefix": "scribe",
        "public_dns": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "scribe_client_secret",
    },
    "represent": {
        "display_name": "Represent",
        "url_prefix": "represent",
        "public_dns": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "represent_client_secret",
    },
    # No disable_pkce: nib's openidconnect client does S256 out of the box, and
    # Kanidm requires PKCE unless a client opts out.
    "nib": {
        "display_name": "Nib",
        "url_prefix": "nib",
        "public_dns": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "nib_client_secret",
    },
}

# Kanidm person accounts. Credential setup is one-shot: the deploy generates a
# reset token, saves it to BW ({username}_reset_token field), and prints the URL.
# Visit the URL once to set your password/passkey.
KANIDM_PERSONS = {
    "bob": {
        "display_name": "The Bob",
        "email": "bob@bob",
    },
}

# Subdomain registry, derived from each service's `url_prefix` (plus any
# `aliases`). Every entry below has `"public_dns": True` so its name lands in
# Cloudflare as an A record pointing to the LAN IP — this prevents macOS /
# iOS resolvers from negatively caching NXDOMAIN when a roaming client (or
# a freshly-rebooted Pi) briefly fails to resolve via Pi-hole. The CF
# records still point at RFC1918 space, so they only connect from LAN /
# WireGuard. Drop a service back to LAN-only by removing its `public_dns`
# flag — it then only gets the Pi-hole split-DNS override and resolves
# nowhere else. Wildcard TLS cert covers both via DNS-01.
# Names listed here are looked up in module globals — a service that's
# been retired (its dict commented out) drops out automatically instead
# of triggering a NameError.
_SUBDOMAIN_NAMES = (
    "HALO",
    "PIHOLE",
    "NTFY",
    "YARR",
    "NAVIDROME",
    "TRACKER",
    "PARTY",
    "DICE",
    "SYNCTHING",
    "KANIDM",
    "AI",
    "COMFY",
    "STT",
    "TTS",
    "MCP_CHAT",
    "SHELF",
    "RASPI_DASHBOARD",
    "OCULAR",
    "SUPERSAW",
    "ZOT",
)
_SUBDOMAIN_SOURCES = tuple(d for d in (globals().get(n) for n in _SUBDOMAIN_NAMES) if d is not None)
PUBLIC_SUBDOMAINS = tuple(
    sorted(
        {svc["url_prefix"] for svc in _SUBDOMAIN_SOURCES if svc.get("public_dns")}
        | {
            alias
            for svc in _SUBDOMAIN_SOURCES
            if svc.get("public_dns")
            for alias in svc.get("aliases", ())
        }
        | {c["url_prefix"] for c in KANIDM_OIDC_CLIENTS.values() if c.get("public_dns")}
    )
)
INTERNAL_SUBDOMAINS = tuple(
    sorted(
        {svc["url_prefix"] for svc in _SUBDOMAIN_SOURCES if not svc.get("public_dns")}
        | {
            alias
            for svc in _SUBDOMAIN_SOURCES
            if not svc.get("public_dns")
            for alias in svc.get("aliases", ())
        }
        | {c["url_prefix"] for c in KANIDM_OIDC_CLIENTS.values() if not c.get("public_dns")}
    )
)
SUBDOMAINS = tuple(sorted(set(PUBLIC_SUBDOMAINS) | set(INTERNAL_SUBDOMAINS)))
