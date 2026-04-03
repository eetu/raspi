# raspi

Automated setup for a Raspberry Pi 4 home server. Deploys and configures all services from scratch on a fresh Raspberry Pi OS Lite (64-bit) install.

## What runs on it

| Service | Purpose |
|---|---|
| [Pi-hole](https://pi-hole.net) | Network-wide ad and tracker blocking, DNS server |
| [WireGuard](https://www.wireguard.com) + [wg-portal](https://github.com/h44z/wg-portal) | VPN for secure access from outside the LAN |
| [Traefik](https://traefik.io) | Reverse proxy with automatic HTTPS (wildcard cert via Let's Encrypt + Cloudflare DNS) |
| [HCC](https://github.com/eetu/hcc) | Home control dashboard |
| [Audiobookshelf](https://www.audiobookshelf.org) | Audiobook server, reads from NAS over CIFS |

HCC and Audiobookshelf run as rootless Podman containers (quadlets) — lighter than Docker, daemonless, managed by systemd. Everything else runs as native binaries.

## Prerequisites

- Raspberry Pi 4 with a fresh Raspberry Pi OS Lite 64-bit SD card
  - Set username, SSH public key, hostname and WiFi in Raspberry Pi Imager before flashing
- [uv](https://docs.astral.sh/uv/) on your Mac
- [Bitwarden CLI](https://bitwarden.com/help/cli/) (`bw`) with a `raspi` folder containing all required secrets (see below)
- Domain managed in Cloudflare DNS

## Secrets

All secrets are stored in Bitwarden under a `raspi` folder. Pyinfra fetches them locally at deploy time and writes them to `/etc/secrets/` on the Pi. Nothing sensitive is committed to this repo.

| Bitwarden item | Contains |
|---|---|
| `hcc` | HCC environment variables (API keys, Hue bridge, room config) |
| `pihole` | Pi-hole admin password |
| `cifs-audiobooks` | NAS share credentials |
| `wireguard-portal` | wg-portal admin credentials |
| `wireguard-server-key` | WireGuard server keypair (generated on first deploy) |
| `cloudflare` | Cloudflare API token + zone ID |

## Setup

**1. Configure**

```sh
cp group_data/all.example.py group_data/all.py   # fill in IPs, domain, NAS share
cp inventory.example.py inventory.py              # fill in SSH host and key path
```

**2. Install dependencies**

```sh
uv sync
```

**3. Unlock Bitwarden**

```fish
set -x BW_SESSION (bw unlock --raw)
bw sync
```

**4. Deploy**

```fish
uv run pyinfra inventory.py deploy.py
```

**5. Manual steps after first deploy**

- Router: add DHCP DNS server → Pi's LAN IP
- Router: add NAT rule UDP 51820 → Pi's LAN IP (for WireGuard)
- Cloudflare: add DNS A records (or let the deploy task handle it automatically)

## Services

All services are accessible via HTTPS on subdomains of the configured domain. They are only reachable from the LAN or over WireGuard — ports are firewalled from the internet.

| URL | Service |
|---|---|
| `hcc.yourdomain.com` | HCC dashboard |
| `pihole.yourdomain.com` | Pi-hole admin |
| `abs.yourdomain.com` | Audiobookshelf |
| `vpn.yourdomain.com` | WireGuard peer management |

## Re-deploying

The deploy is fully idempotent. Run it any time to apply config changes or upgrade packages:

```fish
set -x BW_SESSION (bw unlock --raw) && bw sync
uv run pyinfra inventory.py deploy.py
```

Package upgrades are rate-limited to once per 24 hours. Security patches apply automatically via `unattended-upgrades`.
