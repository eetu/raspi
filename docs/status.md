# Uptime Kuma setup

Uptime Kuma v2 requires a one-time first-run through the web UI to initialize the database and create the admin account. After that, a pre-deployed script seeds all default monitors, the ntfy notification, and a status page directly into SQLite.

## First run

1. Open `https://status.yourdomain.com` (requires LAN or WireGuard)
2. Select **Embedded SQLite** as the database — do not change this after setup
3. Create the admin account using credentials from Bitwarden `raspi/kuma-uptime`
4. Log in once to confirm everything works

The app writes its database to `/var/lib/uptime-kuma/kuma.db` on the host.

## Seed default monitors

After completing the first-run web UI, run the dedicated pyinfra task from your Mac:

```sh
uv run pyinfra inventory.py tasks/kuma_setup.py
```

Or directly on the Pi:

```sh
sudo python3 /usr/local/bin/kuma-setup.py
```

The script stops the container, injects data into SQLite, and restarts it. It is idempotent — safe to re-run, skips anything that already exists.

**What gets created:**

| Monitor | Type | Target |
|---|---|---|
| HCC | HTTP | `https://hcc.yourdomain.com` |
| Pi-hole | HTTP | `https://pihole.yourdomain.com/admin` |
| Audiobookshelf | HTTP | `https://abs.yourdomain.com` |
| WireGuard portal | HTTP | `https://vpn.yourdomain.com` |
| ntfy | HTTP | `https://ntfy.yourdomain.com` |
| Pi | Ping | Pi LAN IP |
| NAS | Ping | NAS IP |
| Pi-hole DNS | DNS | `pi-hole.net` via Pi LAN IP |

A status page at `https://status.yourdomain.com/status/home` groups all monitors under **Services**.

An ntfy notification is created and linked to all monitors — alerts go to the `raspi-alerts` topic automatically.

## Container health monitoring (optional)

Uptime Kuma has a **Docker Container** monitor type that checks if a specific container is running (distinct from HTTP health checks). To enable it, mount the Podman socket into the Uptime Kuma container by adding this line to the quadlet in `tasks/uptime_kuma.py`:

```ini
Volume=/run/podman/podman.sock:/var/run/docker.sock:ro
```

Then add monitors of type **Docker Container** for: `hcc`, `audiobookshelf`, `ntfy`, `diun`.

Note: `podman.socket` must be enabled (already done as part of the Diun setup).

## Traefik monitoring

Traefik does not expose a dedicated health endpoint by default. Monitoring the frontend URLs (which all route through Traefik) is sufficient — if Traefik is down, all HTTP monitors will fail simultaneously, making the cause obvious.

To add an explicit Traefik health check, enable the Traefik API in `tasks/traefik.py`:

```yaml
api:
  dashboard: false
  insecure: false

ping: {}
```

Then add an HTTP monitor for `http://127.0.0.1:8082/ping` (Traefik ping endpoint, internal only).

## Troubleshooting the setup script

If the script fails, inspect the database schema directly:

```sh
sqlite3 /var/lib/uptime-kuma/kuma.db ".tables"
sqlite3 /var/lib/uptime-kuma/kuma.db ".schema monitor"
sqlite3 /var/lib/uptime-kuma/kuma.db ".schema notification"
sqlite3 /var/lib/uptime-kuma/kuma.db ".schema monitor_group"
```

The schema can differ between Uptime Kuma versions. If column names have changed, adjust the INSERT statements in `/usr/local/bin/kuma-setup.py` on the Pi accordingly, then re-run.

Verify what was inserted:

```sh
sqlite3 /var/lib/uptime-kuma/kuma.db "SELECT id, name, type FROM monitor;"
sqlite3 /var/lib/uptime-kuma/kuma.db "SELECT id, name FROM notification;"
sqlite3 /var/lib/uptime-kuma/kuma.db "SELECT slug, title FROM status_page;"
```
