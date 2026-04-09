# Raspi IaC

Agentless infrastructure-as-code for a Raspberry Pi 4 (1 GB) home server, using **pyinfra** (Python, SSH-only, no agents).

## Deploy

```fish
set -x BW_SESSION (bw unlock --raw)   # unlock Bitwarden first
uv run pyinfra inventory.py deploy.py
```

Idempotent — safe to re-run at any time.

## Validate before commit

```fish
uv run ruff check .      # lint
uv run ruff format .     # format
```

No dry-run mode in pyinfra — linting + a careful read is the pre-commit check.

## Key files

| File | Purpose |
|---|---|
| `deploy.py` | Entry point — ordered list of `local.include()` task files |
| `inventory.py` | SSH target (Pi IP, user, key) |
| `group_data/all.py` | All service config (ports, versions, images) — gitignored |
| `group_data/all.example.py` | Template for `all.py` — keep in sync when adding services |
| `vault.py` | Bitwarden CLI helpers — secrets fetched at deploy time |
| `tasks/` | One file per service |

## Service patterns

### Native binary (Traefik, wg-portal, Yarr)
Use when: single static binary, no container needed.

1. Download binary from GitHub releases, version-stamped to `/usr/local/bin/.{service}-version`
2. Create data dir under `/var/lib/{service}/`
3. Write secrets to `/etc/secrets/{service}.env` (mode 600)
4. Write systemd unit to `/etc/systemd/system/{service}.service`
5. `systemd.service(running=True, enabled=True, daemon_reload=True)`
6. Hash-based restart detection (stamp file under `/etc/systemd/system/`)

### Podman Quadlet (Vaultwarden, Gatus, ntfy, ABS, HCC)
Use when: upstream provides a container image.

1. Resolve image tag via `tasks/util.resolve_latest()` if `resolve_latest=True`
2. Create data dir under `/var/lib/{service}/`
3. Write secrets to `/etc/secrets/{service}.env` (mode 600)
4. Write quadlet to `/etc/containers/systemd/{service}.container`
5. Run `/usr/lib/systemd/system-generators/podman-system-generator` to regenerate units
6. `systemd.service(running=True, daemon_reload=True)`
7. Hash-based restart: separate stamps for quadlet hash and env file hash

## Adding a new service — checklist

- [ ] `group_data/all.py` — add config dict (host, port, version/image)
- [ ] `group_data/all.example.py` — mirror the same dict with placeholder values
- [ ] `vault.py` — add helper function + docstring entry if secrets needed
- [ ] `tasks/{service}.py` — new task file following the pattern above
- [ ] `tasks/traefik.py` — add router + service to `dynamic_yaml`, import from `all`
- [ ] `tasks/cloudflare_dns.py` — add subdomain to the list in `configure_dns()`
- [ ] `deploy.py` — add `local.include("tasks/{service}.py")`
- [ ] Bitwarden — create item in `raspi` folder before deploying

## Secrets (Bitwarden)

Items live in a Bitwarden folder named `raspi`. See `vault.py` docstring for the full item list and field structure. The `BW_SESSION` env var must be set before deploy — pyinfra fetches secrets locally at deploy time and writes them to `/etc/secrets/` on the Pi (never committed to git).

## Traefik

- TLS via Cloudflare DNS challenge (wildcard cert for `*.{domain}`)
- Static config: `/etc/traefik/static.yaml`
- Dynamic config: `/etc/traefik/dynamic/services.yaml` (file provider, hot-reload)
- All services bind to `127.0.0.1:{port}` — Traefik is the only public listener
- Adding a service: add router + service block to `dynamic_yaml` in `tasks/traefik.py`

## Ports in use

| Port | Service |
|---|---|
| 80 / 443 | Traefik |
| 3000 | HCC |
| 3001 | Gatus |
| 5335 | Unbound (DNS) |
| 7070 | Yarr |
| 8080 | Pi-hole web UI |
| 8085 | Vaultwarden |
| 8088 | Pi-hole DNS |
| 8090 | ntfy |
| 8888 | wg-portal |
| 13378 | Audiobookshelf |
| 51820 | WireGuard (UDP) |

## Memory budget (Pi 4, 1 GB)

Services are capped via `MemoryMax` in systemd units. Avoid adding Postgres or other heavyweight databases — prefer SQLite or embedded storage.
