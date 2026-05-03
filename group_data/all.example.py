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

UNBOUND = {
    "port": 5335,
    "msg_cache_mb": 50,  # message cache — increase to 100 if you have RAM to spare
    "rrset_cache_mb": 100,  # RRset cache should be ~2x msg_cache
}

PIHOLE = {
    "host": "127.0.0.1",
    "web_port": 8080,  # moved off 80 so Traefik owns it
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

HCC = {
    "host": "127.0.0.1",
    "port": 3000,
    "image": "ghcr.io/eetu/hcc:main",
    # Plain config env vars. See hcc/backend/src/settings.rs.
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
        "HCC_HISTORY_RETENTION_DAYS": "0",
        "SOLIS_STATION_ID": "",
        "SOLIS_BASE_URL": "https://www.soliscloud.com:13333",
    },
    # Secrets sourced from Bitwarden item `hcc`. Map: env var name -> BW field name.
    # Each entry must exist as a hidden field on the BW item before deploy.
    # tasks/secrets.py writes these to /etc/secrets/hcc.env at deploy time.
    "secret_env": {
        "TOMORROW_IO_API_KEY": "tomorrow_io_api_key",
        "HUE_BRIDGE_USER": "hue_bridge_user",
        "SOLIS_KEY_ID": "solis_key_id",
        "SOLIS_KEY_SECRET": "solis_key_secret",
    },
}

# One-shot FMI PV forecast runner. Posts JSON to HCC /api/pv/forecast on a timer.
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

AUDIOBOOKSHELF = {
    "host": "127.0.0.1",
    "port": 13378,
    "books_path": "/mnt/audiobooks/OpenAudible/books",
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

HOSTS = {
    "nasname": "192.168.x.y",  # NAS hostname → IP; add any host that needs a static /etc/hosts entry
}

SHELL = "/usr/bin/fish"  # /usr/bin/zsh, usr/bin/bash

CIFS = {
    "audiobooks": {
        "share": "//nasname/audiobooks",  # NetBIOS hostname of your NAS
        "mountpoint": "/mnt/audiobooks",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
    "music": {
        "share": "//nasname/music",
        "mountpoint": "/mnt/music",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
    "movies": {
        "share": "//nasname/movies",
        "mountpoint": "/mnt/movies",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
    # Used by tasks/restic.py for the encrypted backup repository.
    "backups": {
        "share": "//nasname/backups",
        "mountpoint": "/mnt/backups",
        "vers": "2.0",
        "sec": "ntlmsspi",
    },
}

NTFY = {
    "host": "127.0.0.1",
    "port": 8090,
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
    "version": "v2.6",
}

SYNCTHING = {
    "version": "v2.0.16",
    "host": "127.0.0.1",
    "port": 8384,
    "user": "root",
}

NAVIDROME = {
    "host": "127.0.0.1",
    "port": 4533,
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
    "host": "127.0.0.1",
    "port": 8091,  # hub web UI (8090 taken by ntfy)
    "version": "v0.18.7",
    "agent_image": "docker.io/henrygd/beszel-agent:0.18.7",  # Podman Quadlet
}

KANIDM = {
    "host": "127.0.0.1",
    "port": 8443,
    # Pin to a specific release; set resolve_latest=True to track the latest 1.x.
    "image": "docker.io/kanidm/server:1.9.2",
    "resolve_latest": False,
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
        "redirect_path": "/identity/connect/oidc-signin",
        "scopes": ["openid", "profile", "email"],
        "secret_field": "vw_client_secret",
    },
    "gatus": {
        "display_name": "Gatus Monitoring",
        "url_prefix": "status",
        "redirect_path": "/authorization-code/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "gatus_client_secret",
        "disable_pkce": True,
    },
    "wgportal": {
        "display_name": "WireGuard Portal",
        "url_prefix": "vpn",
        "redirect_path": "/api/v0/auth/login/oidc/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "wgportal_client_secret",
    },
    "audiobookshelf": {
        "display_name": "Audiobookshelf",
        "url_prefix": "audiobooks",
        "redirect_path": "/audiobookshelf/auth/openid/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "abs_client_secret",
    },
    "beszel": {
        "display_name": "Beszel Monitoring",
        "url_prefix": "metrics",
        "redirect_path": "/api/oauth2-redirect",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "beszel_client_secret",
    },
    "oauth2-proxy": {
        "display_name": "OAuth2 Proxy Forward-Auth Gateway",
        "url_prefix": "auth",
        "redirect_path": "/oauth2/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "oauth2_proxy_client_secret",
    },
    "memos": {
        "display_name": "Memos",
        "url_prefix": "memo",
        "redirect_path": "/auth/callback",
        "scopes": ["openid", "email", "profile"],
        "secret_field": "memos_client_secret",
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
