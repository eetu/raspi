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

PIHOLE = {
    "host": "127.0.0.1",
    "upstreams": [
        "9.9.9.10",  # Quad9 unfiltered, no DNSSEC (IPv4)
        "149.112.112.10",  # Quad9 unfiltered, no DNSSEC (IPv4)
        "2620:fe::10",  # Quad9 unfiltered, no DNSSEC (IPv6)
        "2620:fe::fe:10",  # Quad9 unfiltered, no DNSSEC (IPv6)
    ],
    "web_port": 8080,  # moved off 80 so Traefik owns it
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

CIFS = {
    "share": "//nasname/sharename",  # NetBIOS hostname of your NAS
    "host_ip": "192.168.x.y",  # current IP — update if it changes
    "mountpoint": "/mnt/audiobooks",
    "vers": "2.0",
    "sec": "ntlmsspi",
}

NTFY = {
    "host": "127.0.0.1",
    "port": 8090,
    "image": "docker.io/binwiederhier/ntfy:v2",
    "topic": "raspi-alerts",  # topic used by system notifications (Trivy, version checks)
}

UPTIME_KUMA = {
    "host": "127.0.0.1",
    "port": 3001,
    "image": "docker.io/louislam/uptime-kuma:2",
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
