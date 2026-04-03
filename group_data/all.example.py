# Copy this file to all.py and fill in your values.
# all.py is gitignored — never commit it.

NETWORK = {
    "lan_cidr": "192.168.x.0/24",  # your LAN subnet
    "lan_ip": "192.168.x.y",  # static IP reserved for the Pi
    "router": "192.168.x.1",  # your router
    "domain": "yourdomain.com",  # domain managed in Cloudflare
}

WIREGUARD = {
    "subnet": "10.8.0.0/24",  # VPN subnet — change if it conflicts with your LAN
    "ip": "10.8.0.1",  # Pi's VPN address
    "port": 51820,
}

PIHOLE = {
    "host": "127.0.0.1",
    "dns1": "9.9.9.10",  # Quad9 unfiltered, no DNSSEC
    "dns2": "149.112.112.10",
    "web_port": 8080,  # moved off 80 so Traefik owns it
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
    "version": "v2.33.1",
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
    "share": "//192.168.x.y/sharename",  # your NAS share
    "mountpoint": "/mnt/audiobooks",
    "vers": "2.0",
    "sec": "ntlmsspi",
}
