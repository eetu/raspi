# raspi

Automated setup for a Raspberry Pi 4 home server. Deploys and configures all services from scratch on a fresh Raspberry Pi OS Lite (64-bit) install.

## What runs on it

| Service | Purpose |
|---|---|
| [Pi-hole](https://pi-hole.net) | Network-wide ad and tracker blocking, DNS server |
| [Unbound](https://nlnetlabs.nl/projects/unbound/) | Recursive DNS resolver (upstream for Pi-hole) |
| [WireGuard](https://www.wireguard.com) + [wg-portal](https://github.com/h44z/wg-portal) | VPN for secure access from outside the LAN |
| [Traefik](https://traefik.io) | Reverse proxy with automatic HTTPS (wildcard cert via Let's Encrypt + Cloudflare DNS) |
| [HCC](https://github.com/eetu/hcc) | Home control dashboard |
| [fmi-pv-forecast-runner](https://github.com/eetu/fmi-pv-forecast-runner) | One-shot timer (every 3h) — fetches FMI PV forecast and POSTs to HCC `/api/pv/forecast` |
| [Audiobookshelf](https://www.audiobookshelf.org) | Audiobook server, reads from NAS over CIFS |
| [Navidrome](https://www.navidrome.org) | Music streaming server, reads from NAS over CIFS |
| [Yarr](https://github.com/nkanaev/yarr) | Self-hosted RSS reader |
| [ntfy](https://ntfy.sh) | Self-hosted push notification server |
| [Gatus](https://github.com/TwiN/gatus) | Service monitoring and status page |
| [Vaultwarden](https://github.com/dani-garcia/vaultwarden) | Self-hosted Bitwarden-compatible password vault |
| [Trivy](https://github.com/aquasecurity/trivy) | CVE vulnerability scanner |
| [Syncthing](https://syncthing.net) | Peer-to-peer file synchronization |
| [VuIO](https://github.com/vuiodev/vuio) | DLNA media server for LAN movie streaming (auto-discovered by VLC) |
| [Beszel](https://github.com/henrygd/beszel) | Lightweight server monitoring — CPU, memory, disk, network, containers |
| [Kanidm](https://kanidm.com) | Identity management — SSO/OIDC provider for all services that support it |
| [oauth2-proxy](https://oauth2-proxy.github.io/oauth2-proxy/) | Forward-auth gateway gating Pi-hole, Yarr, Navidrome and Syncthing behind Kanidm SSO |
| [restic](https://restic.net) | Encrypted incremental backups of service state to the NAS, with one-shot restore on a fresh Pi |

HCC, Audiobookshelf, Navidrome, ntfy, Gatus, Vaultwarden, Kanidm and the Beszel agent run as Podman containers (quadlets) — daemonless, managed by systemd. Traefik, wg-portal, oauth2-proxy, Yarr, VuIO, Syncthing and other services run as native binaries.

## Prerequisites

- Raspberry Pi 4 with a fresh Raspberry Pi OS Lite 64-bit SD card
  - Set username, SSH public key, hostname and WiFi in Raspberry Pi Imager before flashing
- [uv](https://docs.astral.sh/uv/) on your Mac
- [Bitwarden CLI](https://bitwarden.com/help/cli/) (`bw`) with a `raspi` folder containing all required secrets (see below)
- Domain managed in Cloudflare DNS

## Secrets

All API tokens and credentials are stored in Bitwarden under a `raspi` folder. Pyinfra fetches them locally at deploy time and writes them to `/etc/secrets/` on the Pi.

Non-secret service config (ports, hostnames, base URLs, room layouts, PV system params, etc.) lives in `group_data/all.py`, which is gitignored. Per-service env files merge BW-sourced secrets (`/etc/secrets/{svc}.env`, loaded via `EnvironmentFile=`) with inline plain config from `all.py` (rendered as `Environment=` lines in the unit/quadlet).

For HCC, the `secret_env` dict in `all.py` maps each env var name to its hidden field on the BW `hcc` item — explicit, reviewable, and easy to audit when adding new integrations.

| Bitwarden item | Contains |
|---|---|
| `hcc` | One hidden field per `HCC["secret_env"]` value in `all.py` (e.g. `tomorrow_io_api_key`, `solis_key_id`, `solis_key_secret`, `hue_bridge_user`) |
| `pihole` | Pi-hole admin password |
| `cifs` | NAS share credentials — per-share fields: `{share}_username`, `{share}_password` (hidden), keyed by CIFS dict entries in `all.py` |
| `audiobookshelf` | ABS admin credentials (`login`), scoped API key written back by deploy (`api_key` hidden field — leave empty before first deploy) |
| `navidrome` | Navidrome admin credentials (`login`) |
| `syncthing` | Syncthing web UI credentials (`login`) |
| `wireguard-portal` | wg-portal admin credentials |
| `wireguard-server-key` | WireGuard server keypair (generated on first deploy) |
| `cloudflare` | Cloudflare API token + zone ID |
| `dockerhub` | Docker Hub username + personal access token (avoids unauthenticated pull rate limits) |
| `vaultwarden` | Admin password (plain text, `password` field) + argon2 hash (`admin_token` hidden field) + Gmail app password (`smtp_password` hidden field) |
| `beszel` | Beszel hub admin email (`username`) + password — seeds the hub UI user and is kept in sync with both the PocketBase superuser and regular user on every deploy |
| `kanidm` | All fields written by the deploy on first run — create an empty login item named `kanidm`. Populated fields: `admin_password` and `idm_admin_password` (hidden, recovered via `kanidmd recover-account`), `{client}_client_secret` per OIDC client, `{username}_reset_token` per person |
| `oauth2-proxy` | Empty login item — the deploy generates and stores `cookie_secret` (hidden) on first run; the OIDC `client_secret` lives on the `kanidm` item |
| `restic` | Repository encryption password (`password` field) — load-bearing; losing this means every snapshot is permanently unreadable |
| `asus-router` | SSH key pair for router firewall automation (optional, see below) |

## Setup

**1. Configure**

```sh
cp group_data/all.example.py group_data/all.py   # fill in IPs, domain, NAS share, HCC config, PV system params
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
| `audiobooks.yourdomain.com` | Audiobookshelf |
| `music.yourdomain.com` | Navidrome |
| `rss.yourdomain.com` | Yarr RSS reader |
| `vpn.yourdomain.com` | WireGuard peer management |
| `ntfy.yourdomain.com` | Push notification server |
| `status.yourdomain.com` | Gatus status page |
| `vault.yourdomain.com` | Vaultwarden password vault |
| `syncthing.yourdomain.com` | Syncthing file sync UI |
| `metrics.yourdomain.com` | Beszel monitoring dashboard |
| `idm.yourdomain.com` | Kanidm identity management |
| `auth.yourdomain.com` | oauth2-proxy SSO gateway (forward-auth — visited via service redirects, not directly) |

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

## Audiobookshelf mobile app setup

The deploy creates a scoped API key in ABS (named `mobile`, acts on behalf of the root user) and writes it to Bitwarden (`raspi/audiobookshelf → api_key` hidden field). Retrieve it and enter it in the app:

1. Unlock Bitwarden and run `bw get item audiobookshelf` — copy the `api_key` field value
2. Open the app → set the server URL to `https://audiobooks.yourdomain.com`
3. Enter the API key when prompted

**To rotate the API key:** clear the `api_key` field in Bitwarden and redeploy — the old key is deleted and a new one is created.

The library is created automatically by the deploy and syncs from `/mnt/audiobooks/OpenAudible/books` on the NAS. New books are detected by the file watcher instantly; a full rescan runs every hour.

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

## Kanidm (SSO/OIDC) setup

Kanidm provides single sign-on for services that support OIDC (starting with Vaultwarden). The deploy fully automates Kanidm setup — no manual configuration needed for the server itself.

**What the deploy does automatically:**

1. Starts the Kanidm container with TLS (reuses Traefik's ACME wildcard cert via a systemd path watcher)
2. Bootstraps the admin password from Bitwarden (one-shot, not kept in container env)
3. Creates person accounts defined in `KANIDM_PERSONS` in `all.py`
4. Generates credential reset tokens and saves them to Bitwarden
5. Configures OAuth2/OIDC clients defined in `KANIDM_OIDC_CLIENTS` in `all.py`

**After the first deploy — set up your credentials:**

1. Retrieve your credential reset token from Bitwarden:
   ```sh
   bw get item kanidm | jq -r '.fields[] | select(.name == "yourusername_reset_token") | .value'
   ```
2. Visit the URL printed by the token — it opens Kanidm's credential setup page
3. Set a password and/or register a passkey
4. You can now log in to `https://idm.yourdomain.com` and to any OIDC-enabled service (e.g. Vaultwarden) using your Kanidm identity

**Adding a new person:**

Add an entry to `KANIDM_PERSONS` in `group_data/all.py` and redeploy. The reset token will be saved to Bitwarden and printed during deploy.

**Adding OIDC to a new service:**

1. Add an entry to `KANIDM_OIDC_CLIENTS` in `group_data/all.py` (set `disable_pkce=True` if the client doesn't support PKCE)
2. In the service's task, read the client secret via `bw.kanidm_oidc_secret(KANIDM_OIDC_CLIENTS[name]["secret_field"])` and only configure SSO when it is non-empty (see `tasks/secrets.py` and `tasks/wg_portal.py` for examples)
3. First deploy registers the client in Kanidm and saves the generated secret to Bitwarden. A second deploy then propagates it into the service's env file and turns SSO on.

OIDC is fully optional — services that aren't in `KANIDM_OIDC_CLIENTS` deploy normally without any SSO configuration. You can also leave the dict empty entirely if you don't want to use Kanidm SSO for any service.

## oauth2-proxy (forward-auth for services without native OIDC)

Services that don't support Kanidm OIDC directly are gated via oauth2-proxy running at `auth.{domain}`. Traefik's `forwardAuth` middleware redirects unauthenticated requests to the Kanidm login page; after login the session cookie (scoped to `.{domain}`) is shared across all gated subdomains.

**Gated services:** Pi-hole, Yarr, Navidrome (web UI + Subsonic API), Syncthing

**Exempted paths (no auth required):**
- Pi-hole `/api/info/version` — used by Gatus uptime checks
- Syncthing `/rest/noauth/health` — used by Gatus uptime checks

Subsonic clients reach Navidrome via the IAP/SSO browser flow (e.g. Flo's "Login with IAP") — they complete the Kanidm login in a webview and reuse the resulting cookie for `/rest` calls. When `oauth2-proxy` is not configured for the deployment, Navidrome's music router runs without middleware and the deploy bootstraps an admin user from the `navidrome` Bitwarden item so plain Subsonic clients can log in with username/password.

No manual setup needed — oauth2-proxy is fully provisioned by the deploy (binary, config, systemd unit, Kanidm OIDC client, cookie secret in Bitwarden).

## IPv6 DDNS and router firewall automation

The DDNS timer runs every 5 minutes and updates the `wg.<domain>` AAAA record in Cloudflare when the Pi's global IPv6 changes (ISPs periodically rotate the /64 prefix).

**Optional: automatic router firewall update (Asus routers)**

Tested on Asus routers with stock Asuswrt firmware. Other routers may work if they support SSH, JFFS persistent scripts, and `ip6tables`, but are not supported by this repo.

If your router's IPv6 firewall pins the WireGuard rule to a specific host address, it will break when the prefix rotates. The DDNS script can SSH into the router on prefix change and swap the `ip6tables` rule automatically.

**Router setup (one-time):**

1. Enable SSH (LAN only) on the router admin UI
2. Copy the example script and set your Pi's MAC address:
   ```sh
   cp files/router-update-wg-firewall.sh.example files/router-update-wg-firewall.sh
   # edit PI_MAC in files/router-update-wg-firewall.sh (ip link show eth0 | grep ether)
   ```
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

## Security hardening

### Filesystem sandboxing

All native binary services run in hardened systemd units with:

- **`ProtectSystem=strict`** — root filesystem is read-only; only the service's own data directory is writable via `ReadWritePaths`
- **`ProtectHome=yes`** — `/home`, `/root`, `/run/user` are invisible (except Syncthing, which needs its sync paths)
- **`PrivateTmp=yes`** — isolated `/tmp` per service
- **`CapabilityBoundingSet=`** — all Linux capabilities dropped (except where required, e.g. `CAP_NET_BIND_SERVICE` for Traefik)
- **`ProtectKernelTunables/Modules/ControlGroups`**, **`RestrictNamespaces`**, **`LockPersonality`** — prevent kernel and namespace manipulation

A compromised binary can only write to its own data directory — it cannot read `/etc/secrets/`, other services' data, or modify system files.

Podman container services get filesystem isolation from the container runtime itself (only explicitly mounted volumes are accessible).

### Network egress restrictions

LAN-only services are blocked from reaching the internet via **nftables cgroup-based filtering** (`tasks/network_restrict.py`). This mitigates supply chain attacks where a compromised binary or container image tries to phone home.

**Restricted services:** Audiobookshelf, Navidrome, ntfy, oauth2-proxy, Syncthing, wg-portal, VuIO, Beszel hub, Beszel agent

**Allowed destinations:** localhost, LAN CIDR, WireGuard subnet, SSDP multicast (239.255.255.250)

**Not restricted** (require internet): Traefik (ACME certs), Yarr (RSS feeds), Gatus (uptime checks), Vaultwarden (SMTP), Kanidm (OIDC provider), Unbound (recursive DNS), Pi-hole (blocklists), Trivy (CVE database), DDNS (Cloudflare API), HCC (Tomorrow.io, SolisCloud, Hue discovery), fmi-pv-forecast (FMI open data).

Blocked connection attempts are logged to the kernel journal with `BREACH:<service>:` prefix, including the destination IP.

### Network breach monitoring

A timer runs every 15 minutes, checks the journal for `BREACH:` entries, and sends an urgent ntfy notification with the service name, packet count, and destination IP. This alerts you when:

- A service update introduces unexpected outbound connections
- A network restriction is too aggressive and breaks functionality

VuIO is a LAN-only DLNA service and does not have a Traefik router or Cloudflare DNS entry — it is only accessible via UPnP/DLNA discovery on the local network.

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

## Backups and disaster recovery

Service state (Vaultwarden DB, Kanidm DB, Navidrome library/playlists, Yarr feeds, Audiobookshelf metadata, Syncthing index, wg-portal DB, Beszel hub DB, Traefik `acme.json`) is snapshotted to the NAS as an **encrypted, deduplicated, incremental** restic repository. Music/audiobooks/movies are not backed up — they already live on the NAS.

**Schedule**

- Daily backup — runs at 03:30 with 15-minute jitter, takes ~15 seconds for a delta snapshot, peaks ~150MB RAM. Drives a `restic forget` afterwards to enforce retention (`{daily: 7, weekly: 4, monthly: 6}` by default).
- Weekly prune — runs Sunday 04:30 with `--max-unused 100M` to bound RAM use, locks the repo (declared `Conflicts=raspi-backup.service` so the two timers can never overlap), and runs a 5%-data-pack `restic check` to detect bit-rot on the NAS. ntfy alerts on failure.

**Configuration** lives under `RESTIC` in `group_data/all.py`: paths to back up, paths to exclude (default excludes Navidrome's regenerable cache + artwork dirs), retention, schedules, prune cap. Removing the `RESTIC` dict from `all.py` opts the host out — `tasks/restic.py` becomes a no-op.

**Storage** — the `backups` CIFS share (configured in `CIFS["backups"]`) is mounted via systemd automount; credentials come from the standard `cifs` Bitwarden item using `backups_username` / `backups_password` fields. The encryption password is a separate, load-bearing secret on a dedicated `restic` Bitwarden item.

**Restore on a blank Pi**

After re-flashing the SD card, the deploy can restore the latest snapshot before any service starts up:

- **Cold start (NAS share not yet mounted at plan time)** — set the env var:
  ```fish
  set -x BW_SESSION (bw unlock --raw)
  RESTORE=true uv run pyinfra inventory.py deploy.py
  ```
- **Re-bootstrap with the share already mounted from a prior deploy** — the deploy detects `/var/lib/vaultwarden` missing AND a repo present at `/mnt/backups/raspi-restic`, then prompts interactively before queuing the restore step.

A `/var/lib/.restic-restored` stamp file makes the operation idempotent — subsequent deploys never restore again unless the stamp is removed.

**Manual operations** — when you want to inspect or operate on the repo from the Pi:

```sh
sudo bash -c '. /etc/secrets/restic.env; restic snapshots'
sudo bash -c '. /etc/secrets/restic.env; restic stats'
sudo bash -c '. /etc/secrets/restic.env; restic restore <snapshot-id> --target /tmp/restored'
```

For ad-hoc work that needs more RAM than the Pi can spare, mount the same share from your laptop and run `restic` against it directly — the encryption password from Bitwarden is the only thing required.

## Re-deploying

The deploy is fully idempotent. Run it any time to apply config changes or upgrade packages:

```fish
set -x BW_SESSION (bw unlock --raw) && bw sync
uv run pyinfra inventory.py deploy.py
```

Package upgrades are rate-limited to once per 24 hours. Security patches apply automatically via `unattended-upgrades`.
