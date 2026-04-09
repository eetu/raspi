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

NAVIDROME = {
    "host": "127.0.0.1",
    "port": 4533,
    "image": "docker.io/deluan/navidrome:0.61.1",
    "resolve_latest": False,
}
