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
| [Gatus](https://github.com/TwiN/gatus) | Service monitoring and status page |
| [Vaultwarden](https://github.com/dani-garcia/vaultwarden) | Self-hosted Bitwarden-compatible password vault |
| [Trivy](https://github.com/aquasecurity/trivy) | CVE vulnerability scanner |

HCC, Audiobookshelf, ntfy, Gatus and Vaultwarden run as Podman containers (quadlets) — daemonless, managed by systemd. Trivy and other services run as native binaries.

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
| `dockerhub` | Docker Hub username + personal access token (avoids unauthenticated pull rate limits) |
| `vaultwarden` | Admin password (plain text, `password` field) + argon2 hash (`admin_token` hidden field) + Gmail app password (`smtp_password` hidden field) |
| `asus-router` | SSH key pair for router firewall automation (optional, see below) |

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
- Router: add firewall rule UDP 51820 → Pi's LAN IP (for WireGuard IPv4)
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
| `status.yourdomain.com` | Gatus status page |
| `vault.yourdomain.com` | Vaultwarden password vault |

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

## Vaultwarden setup

Vaultwarden is a self-hosted Bitwarden-compatible vault. Useful for sharing secrets internally — all Bitwarden clients can connect to both bitwarden.com and a self-hosted instance simultaneously.

**1. Generate the admin token hash (once)**

```sh
brew install argon2
printf 'your-password' | argon2 "$(openssl rand -base64 24)" -id -t 3 -m 16 -p 4 -l 32 -e
```

Store the output (`$argon2id$v=19$...`) in a hidden custom field named `admin_token` on the `raspi/vaultwarden` Bitwarden item. Store your plain-text admin password in the `password` field.

**2. Create your user account**

New signups are disabled by default. To register the first account:

1. Go to `https://vault.yourdomain.com/admin` and log in with your plain-text admin password
2. Go to **General Settings** → enable **Allow new signups** → Save
3. Go to `https://vault.yourdomain.com` → **Create Account** → register
4. Back in admin → disable signups again

**3. Connect Bitwarden clients**

In the Bitwarden desktop app or browser extension you can be logged into multiple accounts on different servers simultaneously:

1. Click the account icon → **Add account**
2. Before entering credentials, click the region selector and choose **Self-hosted**
3. Set the server URL to `https://vault.yourdomain.com`
4. Log in with the account you created

## IPv6 DDNS and router firewall automation

The DDNS timer runs every 5 minutes and updates the `wg.<domain>` AAAA record in Cloudflare when the Pi's global IPv6 changes (ISPs periodically rotate the /64 prefix).

**Optional: automatic router firewall update (Asus routers)**

Tested on Asus routers with stock Asuswrt firmware. Other routers may work if they support SSH, JFFS persistent scripts, and `ip6tables`, but are not supported by this repo.

If your router's IPv6 firewall pins the WireGuard rule to a specific host address, it will break when the prefix rotates. The DDNS script can SSH into the router on prefix change and swap the `ip6tables` rule automatically.

**Router setup (one-time):**

1. Enable SSH (LAN only) on the router admin UI
2. Set `PI_MAC` in `files/router-update-wg-firewall.sh` to the Pi's Ethernet MAC (`ip link show eth0 | grep ether`)
3. Copy the script to the router and set up persistence:
   ```sh
   scp files/router-update-wg-firewall.sh USER@ROUTER:/jffs/scripts/update-wg-firewall.sh
   ssh USER@ROUTER chmod +x /jffs/scripts/update-wg-firewall.sh
   ssh USER@ROUTER 'echo "/jffs/scripts/update-wg-firewall.sh" >> /jffs/scripts/firewall-start && chmod +x /jffs/scripts/firewall-start'
   ```
4. Create a Bitwarden SSH key item named `asus-router` in the `raspi` folder
5. Add the public key to the router's authorized_keys with a `command=` restriction:
   ```
   command="/jffs/scripts/update-wg-firewall.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding ssh-ed25519 AAAA... raspi-ddns
   ```
6. Add to `group_data/all.py`:
   ```python
   NETWORK = {
       ...
       "router_user": "your-username",
       "router_ssh_port": 22,
   }
   ```

## Security and update monitoring

All alerts are delivered to the ntfy topic configured in `NTFY["topic"]` (default: `raspi-alerts`).

### Container image updates — Diun

Diun polls container registries every 6 hours and alerts when:
- The digest for a running image tag has changed (e.g. a security patch published under the same tag)
- A newer semver tag exists for any running image (only the 10 most recent clean `vX.Y.Z` tags are checked — arch-specific variants are excluded to keep API calls low)

Docker Hub credentials from Bitwarden are used to avoid unauthenticated pull rate limits.

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
