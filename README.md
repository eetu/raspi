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
| [ntfy](https://ntfy.sh) | Self-hosted push notification server |
| [Uptime Kuma](https://github.com/louislam/uptime-kuma) | Service monitoring dashboard |
| [Diun](https://github.com/crazy-max/diun) | Container image update notifier |
| [Trivy](https://github.com/aquasecurity/trivy) | CVE vulnerability scanner |

HCC, Audiobookshelf, ntfy, Uptime Kuma and Diun run as Podman containers (quadlets) — daemonless, managed by systemd. Trivy and other services run as native binaries.

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
| `kuma-uptime` | Uptime Kuma admin credentials (used for first-run web UI setup) |

## Setup

**1. Configure**

```sh
cp group_data/all.example.py group_data/all.py   # fill in IPs, domain, NAS share
cp inventory.example.py inventory.py              # fill in SSH host and key path
```

**2. Install dependencies and hooks**

```sh
uv sync
./install-hooks.sh
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
| `ntfy.yourdomain.com` | Push notification server |
| `status.yourdomain.com` | Uptime Kuma monitoring |

## ntfy mobile app setup

ntfy lets the Pi (or any service) send push notifications to your phone. The server is self-hosted and only reachable over WireGuard or LAN.

**Install the app**

- iOS: [App Store](https://apps.apple.com/app/ntfy/id1625396347)
- Android: [Google Play](https://play.google.com/store/apps/details?id=io.heckel.ntfy) or [F-Droid](https://f-droid.org/en/packages/io.heckel.ntfy/)

**Connect to your server**

1. Open the ntfy app
2. Tap the settings icon → **Manage accounts** → **Add account**
3. Set the server URL to `https://ntfy.yourdomain.com`
4. No username/password needed (access is controlled at the network level via WireGuard)

**Subscribe to a topic**

1. Tap **+** to add a subscription
2. Enter a topic name, e.g. `raspi-alerts`
3. The app will now receive notifications sent to that topic

**Send a test notification from the Pi**

```sh
curl -d "Test from Pi" https://ntfy.yourdomain.com/raspi-alerts
```

Or from your Mac while on the VPN:

```sh
curl -d "Hello from deploy" https://ntfy.yourdomain.com/raspi-alerts
```

**Receiving notifications off-VPN**

The ntfy server is behind the firewall and only reachable from LAN or WireGuard. To receive notifications when your phone is not on VPN, either:

- Enable [always-on VPN](https://support.apple.com/guide/deployment/vpn-overview-dep0a2cb7686/web) on your device, or
- Open port 443 to the internet on your router (exposes all HTTPS services, not just ntfy)

> The simplest approach: set your WireGuard client to connect automatically on untrusted networks.

## Uptime Kuma setup

Uptime Kuma has no credentials set at deploy time — the first visit creates the admin account.

1. Open `https://status.yourdomain.com` while on LAN or WireGuard
2. Create an admin username and password
3. Add monitors — see suggestions below

**Suggested monitors**

| Name | Type | Target |
|---|---|---|
| HCC | HTTP(s) | `https://hcc.yourdomain.com` |
| Pi-hole | HTTP(s) | `https://pihole.yourdomain.com/admin` |
| Audiobookshelf | HTTP(s) | `https://abs.yourdomain.com` |
| WireGuard portal | HTTP(s) | `https://vpn.yourdomain.com` |
| ntfy | HTTP(s) | `https://ntfy.yourdomain.com` |
| WireGuard port | UDP Port | `yourdomain.com` port `51820` |
| NAS | Ping | NAS hostname or IP |
| Pi | Ping | Pi's LAN IP |
| DNS | DNS | query `pi-hole.net` via Pi's LAN IP |
| TLS cert | HTTP(s) | `https://hcc.yourdomain.com` (enable cert expiry alert) |

**Notifications**

Wire Uptime Kuma alerts into ntfy so you get a push notification when anything goes down:

1. In Uptime Kuma go to **Settings → Notifications → Add notification**
2. Choose type **ntfy**
3. Set server URL to `https://ntfy.yourdomain.com` and topic to e.g. `raspi-alerts`
4. Apply the notification to all monitors

## Security and update monitoring

All alerts are delivered to the ntfy topic configured in `NTFY["topic"]` (default: `raspi-alerts`).

### Container image updates — Diun

Diun polls container registries every 6 hours. It alerts when:
- The digest for a running image tag has changed (e.g. a security patch published under the same tag)
- A newer semver tag exists for any running image (`watchRepo: true`)

No setup required — runs automatically after deploy.

### CVE vulnerability scanning — Trivy

Trivy scans all running container images for HIGH and CRITICAL CVEs once a week. If any are found you get one ntfy notification per affected image with instructions to SSH in for details.

Run a scan manually:

```sh
sudo /usr/local/bin/trivy-cve-scan.sh
```

Or inspect a specific image directly:

```sh
trivy image ghcr.io/advplyr/audiobookshelf:2.33.1
```

### Native binary version checks

A daily timer checks Traefik and wg-portal against their latest GitHub releases and sends an ntfy alert if either is outdated. Re-deploy to update.

Run manually:

```sh
sudo /usr/local/bin/check-versions.sh
```

## Re-deploying

The deploy is fully idempotent. Run it any time to apply config changes or upgrade packages:

```fish
set -x BW_SESSION (bw unlock --raw) && bw sync
uv run pyinfra inventory.py deploy.py
```

Package upgrades are rate-limited to once per 24 hours. Security patches apply automatically via `unattended-upgrades`.
