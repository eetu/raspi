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

WIREGUARD = {
    "subnet": "10.8.0.0/24",  # VPN subnet — change if it conflicts with your LAN
    "ip": "10.8.0.1",  # Pi's VPN IPv4 address
    "subnet6": "fd00::/64",  # VPN IPv6 ULA subnet
    "ip6": "fd00::1",  # Pi's VPN IPv6 address
    "port": 51820,
    # Optional: set if IPv4 WAN is reachable (not behind CGNAT).
    # Enables A record + DDNS for wg endpoint. Omit if behind CGNAT.
    # "public_ipv4": True,
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
    "public": True,
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
    "public": True,
}

# Off-Pi speech-to-text endpoint (Mac mini ../mini repo, whisper.cpp HTTP
# server). Traefik proxies stt.{domain} to the Mini's Caddy on port 8190;
# the Mini owns auth (toggle WHISPER["require_api_key"] in the ../mini repo).
STT = {
    "host": "192.168.x.y",  # Mac mini LAN IP (same as AI)
    "port": 8190,  # Caddy gateway port for Whisper on the Mini
    "url_prefix": "stt",
    "public": True,
}

# Off-Pi text-to-speech endpoint (Mac mini ../mini repo, Piper TTS). Traefik
# proxies tts.{domain} to the Mini's Caddy on port 8192; the Mini owns auth
# (toggle PIPER["require_api_key"] in the ../mini repo).
TTS = {
    "host": "192.168.x.y",  # Mac mini LAN IP (same as AI)
    "port": 8192,  # Caddy gateway port for Piper on the Mini
    "url_prefix": "tts",
    "public": True,
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
    "public": True,
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

# Scribe — self-hosted Audible library mirror. Talks to shim over loopback,
# ships ffmpeg work to scribe-press on the mini. The library/ tree on the
# CIFS audiobooks share is what audiobookshelf reads; original/ is the
# cold-storage AAXC tree (ABS never sees it).
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
    "public": True,  # external clients (iOS app) want this reachable
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
    "public": True,
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

# Optional. Comment the entire block to retire ABS without deleting
# state — tasks/audiobookshelf.py drops into a cleanup branch (stop +
# disable systemd unit, leave /var/lib/audiobookshelf, BW item, and
# Kanidm OIDC client untouched). Re-add the block + redeploy to bring
# the service back online. scribe-shelf covers the read-only API path
# for most clients now.
AUDIOBOOKSHELF = {
    "host": "127.0.0.1",
    "port": 13378,
    "books_path": "/mnt/audiobooks/audible/books",
    # Pinned to a specific tag (no floating major tag available).
    # Set resolve_latest=True to install the latest major.x at deploy time.
    "image": "ghcr.io/advplyr/audiobookshelf:2.33.1",
    "resolve_latest": False,
}

WGPORTAL = {
    "host": "127.0.0.1",
    "port": 8888,
    "version": "v2.2.3",
}

TRAEFIK = {
    "host": "0.0.0.0",
    "version": "v3.6.12",
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

CIFS = {
    "audiobooks": {
        "share": "//zenwifi/audiobooks",  # alias from HOSTS above
        "mountpoint": "/mnt/audiobooks",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
    "music": {
        "share": "//zenwifi/music",
        "mountpoint": "/mnt/music",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
    "movies": {
        "share": "//zenwifi/movies",
        "mountpoint": "/mnt/movies",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
    # Used by tasks/restic.py for the encrypted backup repository.
    "backups": {
        "share": "//zenwifi/backups",
        "mountpoint": "/mnt/backups",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
}

NTFY = {
    "host": "127.0.0.1",
    "port": 8090,
    "url_prefix": "ntfy",
    "public": True,  # external push sources (CI webhooks, alerts) need to reach it
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
        "/var/lib/memos",
        "/var/lib/gatus",
        "/var/lib/yarr",
        "/var/lib/audiobookshelf",
        "/var/lib/syncthing",
        "/var/lib/wg-portal",
        "/var/lib/beszel",
        "/var/lib/chat",
        "/var/lib/scribe",
        "/var/lib/shim",
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
    "public": True,
    "version": "v2.6",
}

SYNCTHING = {
    "version": "v2.0.16",
    "host": "127.0.0.1",
    "port": 8384,
    "url_prefix": "syncthing",
    "public": True,
    "user": "root",
}

NAVIDROME = {
    "host": "127.0.0.1",
    "port": 4533,
    "url_prefix": "music",
    "public": True,
    "image": "docker.io/deluan/navidrome:0.61.1",
    "resolve_latest": False,
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
# CVE status onto one LAN-only page, behind oauth2-proxy (public: False).
RASPI_DASHBOARD = {
    "host": "127.0.0.1",
    "port": 3007,
    "url_prefix": "dashboard",
    "image": "ghcr.io/eetu/raspi-dashboard:main",
    "public": False,
    "memory_max": "96M",
    # Which beszel user (from BESZEL["users"]) this app authenticates as.
    "beszel_user": "dashboard@example.com",
}

# ocular — camera-vision app on a separate camera node (e.g. a Pi 3 B+ with a
# camera). raspi only proxies it: Traefik upstream is the node's LAN IP (the
# AI/COMFY off-host pattern). The camera/detector block is rendered into
# /etc/ocular/config.json on the node by tasks/ocular.py (native deploy, shipped
# from the sibling ../ocular working tree). LAN-only subdomain (no "public").
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
    "public": True,
    # MCP bridge for chat's img2img + inpaint endpoints. Speaks streamable-HTTP
    # MCP at `/mcp`. CHAT_MCP_API_KEY (backend) and CHAT_MCP_SERVER_KEY (this
    # service) are both opt-in — leave unset while we trust the LAN perimeter.
}

KANIDM = {
    "host": "127.0.0.1",
    "port": 8443,
    "url_prefix": "idm",
    "public": True,
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
        "public": True,  # mobile Bitwarden client needs reachable URL on cellular
        "redirect_path": "/identity/connect/oidc-signin",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "vw_client_secret",
    },
    "gatus": {
        "display_name": "Gatus Monitoring",
        "url_prefix": "gatus",
        "public": True,
        "redirect_path": "/authorization-code/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "gatus_client_secret",
        "disable_pkce": True,
    },
    "wgportal": {
        "display_name": "WireGuard Portal",
        "url_prefix": "vpn",
        "public": True,
        "redirect_path": "/api/v0/auth/login/oidc/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "wgportal_client_secret",
    },
    "audiobookshelf": {
        "display_name": "Audiobookshelf",
        "url_prefix": "audiobooks",
        "public": True,
        "redirect_path": "/audiobookshelf/auth/openid/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "abs_client_secret",
    },
    "beszel": {
        "display_name": "Beszel Monitoring",
        "url_prefix": "beszel",
        "public": True,
        "redirect_path": "/api/oauth2-redirect",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "beszel_client_secret",
    },
    "oauth2-proxy": {
        "display_name": "OAuth2 Proxy Forward-Auth Gateway",
        "url_prefix": "auth",
        "public": True,
        "redirect_path": "/oauth2/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "oauth2_proxy_client_secret",
    },
    "memos": {
        "display_name": "Memos",
        "url_prefix": "memo",
        "public": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "memos_client_secret",
    },
    "chat": {
        "display_name": "Chat",
        "url_prefix": "chat",
        "public": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "chat_client_secret",
    },
    "scribe": {
        "display_name": "Scribe",
        "url_prefix": "scribe",
        "public": True,
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "scribe_client_secret",
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
# `aliases`). Every entry below has `"public": True` so its name lands in
# Cloudflare as an A record pointing to the LAN IP — this prevents macOS /
# iOS resolvers from negatively caching NXDOMAIN when a roaming client (or
# a freshly-rebooted Pi) briefly fails to resolve via Pi-hole. The CF
# records still point at RFC1918 space, so they only connect from LAN /
# WireGuard. Drop a service back to LAN-only by removing its `public`
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
)
_SUBDOMAIN_SOURCES = tuple(d for d in (globals().get(n) for n in _SUBDOMAIN_NAMES) if d is not None)
PUBLIC_SUBDOMAINS = tuple(
    sorted(
        {svc["url_prefix"] for svc in _SUBDOMAIN_SOURCES if svc.get("public")}
        | {
            alias
            for svc in _SUBDOMAIN_SOURCES
            if svc.get("public")
            for alias in svc.get("aliases", ())
        }
        | {c["url_prefix"] for c in KANIDM_OIDC_CLIENTS.values() if c.get("public")}
    )
)
INTERNAL_SUBDOMAINS = tuple(
    sorted(
        {svc["url_prefix"] for svc in _SUBDOMAIN_SOURCES if not svc.get("public")}
        | {
            alias
            for svc in _SUBDOMAIN_SOURCES
            if not svc.get("public")
            for alias in svc.get("aliases", ())
        }
        | {c["url_prefix"] for c in KANIDM_OIDC_CLIENTS.values() if not c.get("public")}
    )
)
SUBDOMAINS = tuple(sorted(set(PUBLIC_SUBDOMAINS) | set(INTERNAL_SUBDOMAINS)))
