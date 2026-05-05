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

A save hook runs `ruff format` automatically, which removes unused imports. When adding a new import, always include the code that uses it in the same edit — never add an import alone in one step and the usage in a separate step.

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

### Native binary (Traefik, wg-portal, Yarr, VuIO, Syncthing)
Use when: single static binary, no container needed.

1. Download binary from GitHub releases, version-stamped to `/usr/local/bin/.{service}-version`
2. Create data dir under `/var/lib/{service}/`
3. Write secrets to `/etc/secrets/{service}.env` (mode 600)
4. Write systemd unit to `/etc/systemd/system/{service}.service` with sandboxing (see below)
5. `systemd.service(running=True, enabled=True, daemon_reload=True)`
6. Hash-based restart detection (stamp file under `/etc/systemd/system/`)

### Podman Quadlet (Vaultwarden, Gatus, ntfy, ABS, HCC, Navidrome, Memos, Kanidm)
Use when: upstream provides a container image.

1. Resolve image tag via `tasks/util.resolve_latest()` if `resolve_latest=True`
2. Create data dir under `/var/lib/{service}/`
3. Write secrets to `/etc/secrets/{service}.env` (mode 600)
4. Write quadlet to `/etc/containers/systemd/{service}.container`
5. Run `/usr/lib/systemd/system-generators/podman-system-generator` to regenerate units
6. `systemd.service(running=True, daemon_reload=True)`
7. Hash-based restart: separate stamps for quadlet hash and env file hash

## Refactoring while adding services

When planning a new service, look for opportunities to clean up existing code that the new service makes awkward — duplicated config keys, repeated patterns that can be looped, hardcoded values that should come from `all.py`. Propose these refactors as part of the plan, not as separate follow-up work.

## Adding a new service — checklist

- [ ] `group_data/all.example.py` — add config dict (host, port, version/image)
- [ ] `group_data/all.py` — mirror the same change verbatim (the file holds no secret values; AI assistants may edit it directly)
- [ ] `vault.py` — add helper function + docstring entry if secrets needed
- [ ] `tasks/{service}.py` — new task file following the pattern above
- [ ] `tasks/traefik.py` — add router + service to `dynamic_yaml`, import from `all` (if web-accessible)
- [ ] `tasks/cloudflare_dns.py` — add subdomain to the list in `configure_dns()` (if web-accessible)
- [ ] `tasks/network_restrict.py` — add to `RESTRICTED` list if the service is LAN-only
- [ ] `group_data/all.py` — append `/var/lib/{service}` to `RESTIC["paths"]` if the service has persistent state worth restoring on a blank Pi
- [ ] `deploy.py` — add `local.include("tasks/{service}.py")`
- [ ] Bitwarden — create item in `raspi` folder before deploying

## Secrets handling — AI assistants read this

**Do NOT read secret values into your context.** All live credentials are in
`/etc/secrets/*` on the Pi (env files written by `tasks/secrets.py`) and in
the Bitwarden `raspi` folder. `group_data/all.py` itself contains only
non-secret config plus references to BW field names (e.g. the `secret_env`
dict in `HCC` maps env var → BW field name) — it is safe to read and to edit
when mirroring additions made to `group_data/all.example.py`.

**Banned operations** (these dump plaintext into the conversation transcript):
- `ssh raspi sudo cat /etc/secrets/...`
- `ssh raspi sudo grep ... /etc/secrets/...`
- `ssh raspi -- env` after sourcing a secret file
- Any `echo $SECRET_VAR`, `printenv FOO`, `set | grep ...` that surfaces a value
- Reading raw values from `bw get item ...` (filenames, field *names*, and
  `bw status`/membership checks are fine — values are not)

**Allowed operations** (secret stays inside the shell, never echoed):
- `ssh raspi sudo systemctl restart <svc>` / `status` / `journalctl -u <svc>` (provided the service doesn't log its own secrets)
- `ssh raspi sudo systemd-run --pipe --quiet --property=EnvironmentFile=/etc/secrets/foo.env -- curl -fsS -H "Authorization: Bearer $TOKEN" https://...`
- `ssh raspi 'sudo bash -c ". /etc/secrets/foo.env && curl ... > /tmp/out"'` then read `/tmp/out` (only if output is not the secret itself)
- `ssh raspi sudo ls -la /etc/secrets/` (filenames only, no contents)
- `ssh raspi sudo stat /etc/secrets/foo.env` (size/mode/mtime, no contents)
- `ssh raspi sudo sha256sum /etc/secrets/foo.env` (hash for change detection)

Rule of thumb: it's fine to *use* a secret in a remote command, never to *transport* it into the assistant's context.

## Secrets (Bitwarden)

Items live in a Bitwarden folder named `raspi`. See `vault.py` docstring for the full item list and field structure. The `BW_SESSION` env var must be set before deploy — pyinfra fetches secrets locally at deploy time and writes them to `/etc/secrets/` on the Pi (never committed to git).

CIFS (NAS) credentials are consolidated in a single `cifs` Bitwarden item with per-share fields (`{share}_username`, `{share}_password`). The CIFS dict keys in `all.py` drive which fields are expected — adding a new CIFS mount automatically creates its credential file.

### Rotating a secret

`tasks/secrets.py` is the sole owner of writing all `/etc/secrets/*` files. Service tasks detect secret changes by hashing the on-disk file after it has been written — they never read from Bitwarden directly. To rotate a secret and restart the affected service in one shot:

```fish
uv run pyinfra inventory.py tasks/secrets.py tasks/<service>.py
```

Examples:
- `tasks/secrets.py tasks/hcc.py` — rotate HCC credentials
- `tasks/secrets.py tasks/traefik.py` — rotate Cloudflare API token
- `tasks/secrets.py tasks/cifs.py` — rotate NAS credentials (remounts shares)

## Security hardening

### Filesystem sandboxing (native binaries)
All native binary services use systemd sandboxing: `ProtectSystem=strict` (read-only root filesystem with explicit `ReadWritePaths`), `ProtectHome=yes`, `PrivateTmp=yes`, `ProtectKernelTunables/Modules/ControlGroups`, `RestrictNamespaces`, `LockPersonality`, and `CapabilityBoundingSet` limited to only what the service needs. A compromised binary can only write to its own data directory.

### Network egress restrictions
LAN-only services (audiobookshelf, navidrome, ntfy, wg-portal, vuio) are blocked from reaching the internet via nftables rules with cgroup-based matching (`tasks/network_restrict.py`). Allowed destinations: localhost, LAN CIDR, WireGuard subnet, and SSDP multicast. Blocked attempts are logged with `BREACH:<service>:` prefix in the kernel journal, including destination IP.

### Network breach monitoring
A systemd timer (`tasks/network_monitor.py`) runs every 15 minutes, checks the journal for `BREACH:` entries, and sends an urgent ntfy alert with the service name, blocked packet count, and destination IP.

### Adding network restrictions to a new service
1. Add the service name to the `RESTRICTED` list in `tasks/network_restrict.py`
2. If the service needs specific non-LAN destinations (e.g., SSDP multicast), add an accept rule before the drop rules

## SSO/OIDC (Kanidm)

`tasks/kanidm.py` runs the server. `tasks/kanidm_oidc.py` is the integration step — creates persons and OAuth2 clients via the Kanidm REST API after the server is healthy. **Two-deploy bootstrap is the canonical flow** for any new service that needs the Kanidm client secret: deploy 1 registers the client in Kanidm and writes the generated secret to BW; deploy 2 reads the secret out of BW and wires it into the service. Both deploys are otherwise idempotent.

To wire a new service into SSO:

1. Add an entry to `KANIDM_OIDC_CLIENTS` in `group_data/all.py` (set `disable_pkce=True` if the client doesn't support it). Mirror the change to `group_data/all.example.py`.
2. In the service task, look up the entry with `KANIDM_OIDC_CLIENTS.get(name)` and only configure SSO when both the entry exists *and* `bw.kanidm_oidc_secret(...)` returns non-empty. This keeps OIDC truly optional — removing the entry (or starting with an empty dict) deploys the service without SSO; the empty-secret guard also handles the first deploy where the secret hasn't been generated yet.
3. Place the `local.include("tasks/{service}.py")` line *after* `tasks/kanidm_oidc.py` in `deploy.py` so the secret exists by the time the service task runs on deploy 2.

### Two integration variants

Pick whichever the service supports — the gating logic is the same in both:

**Env-based** (Vaultwarden, Audiobookshelf, wg-portal, Beszel, Gatus): the service reads OIDC config from environment variables. `tasks/secrets.py` writes them to `/etc/secrets/{service}.env` only when `bw.kanidm_oidc_secret(...)` returns non-empty, and the service task includes the env file via `EnvironmentFile=` in its unit/quadlet. No post-deploy API call needed.

**REST-based** (Memos): the service has no OIDC env vars and exposes a REST API to register identity providers post-startup. `tasks/secrets.py` writes the client secret into `/etc/secrets/{service}.env` (along with bootstrap admin credentials), then the service task adds a `server.shell` step that, in order:

1. Sources `/etc/secrets/{service}.env`
2. Polls a readiness probe (`/healthz` or equivalent)
3. POSTs to the user-creation endpoint to bootstrap an admin from BW (`|| true` — repeat calls 4xx once the user exists, that's fine)
4. POSTs to the auth endpoint to obtain a session/token
5. GETs the IdP list and skips if the entry is already present (`grep -qx 'Kanidm'`)
6. POSTs the IdP body with the bearer/cookie obtained in step 4

`tasks/memos.py` is the reference implementation — copy the shell layout when adding a new REST-bootstrapped service.

### When the API contract differs from the docs

When implementing a REST-based variant, **verify the actual API on the running container before trusting upstream docs**. Probing tactics that have proven necessary:

- `curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:{port}/<endpoint>` to find which paths exist (404 vs 401 vs 501 distinguishes "not implemented" from "needs auth" from "wrong path")
- `curl -sS -i ...` to inspect headers — some services use non-standard cookie headers (`Grpc-Metadata-Set-Cookie`) that curl's `-c` jar drops; in those cases parse the access token out of the JSON body and use `Authorization: Bearer ...` instead
- Try both flat `{username, password}` and wrapped `{user: {...}}` body shapes when a 400 says "invalid {field}: " — wrapper conventions vary
- The same applies to enum values (`type: "OAUTH2"` vs integer enum) and field-name casing

Note any version-specific quirks in code comments so the next person reading the task doesn't repeat the discovery work.

## Traefik

- TLS via Cloudflare DNS challenge (wildcard cert for `*.{domain}`)
- Static config: `/etc/traefik/static.yaml`
- Dynamic config: `/etc/traefik/dynamic/services.yaml` (file provider, hot-reload)
- All services bind to `127.0.0.1:{port}` — Traefik is the only public listener
- Adding a service: add router + service block to `dynamic_yaml` in `tasks/traefik.py`

## Backups (restic)

`tasks/restic.py` snapshots service state from `RESTIC["paths"]` to an encrypted repository on the `backups` CIFS share. Daily timer at 03:30 (`raspi-backup.timer`); weekly prune at Sun 04:30 (`raspi-prune.timer`, repo lock declared via `Conflicts=raspi-backup.service` so they cannot overlap, ntfy alert on failure). Repo password lives in the `restic` BW item.

When adding a service with persistent state, append `/var/lib/{service}` to `RESTIC["paths"]` so future blank-slate restores cover it. Add regenerable subdirectories (caches, derived artwork, search indexes) to `RESTIC["excludes"]` to keep snapshots small and avoid overflowing tmpfs `/tmp` during packing.

**Restore-on-blank** runs at deploy time before any service starts. Triggered either interactively (blank Pi + repo present + TTY) or via `RESTORE=true` env var (cold-start case where the NAS share isn't mounted at plan time yet). Idempotent — `/var/lib/.restic-restored` stamp file blocks subsequent restores until removed.

## Ports in use

| Port | Service |
|---|---|
| 80 / 443 | Traefik |
| 3000 | HCC |
| 3001 | Gatus |
| 3002 | Chat |
| 4533 | Navidrome |
| 5230 | Memos |
| 5335 | Unbound (DNS) |
| 7070 | Yarr |
| 8384 | Syncthing web UI |
| 8080 | Pi-hole web UI |
| 8085 | Vaultwarden |
| 8088 | Pi-hole DNS |
| 8090 | ntfy |
| 8091 | Beszel hub |
| 8443 | Kanidm (HTTPS) |
| 8096 | VuIO (DLNA) |
| 8888 | wg-portal |
| 9090 | oauth2-proxy |
| 13378 | Audiobookshelf |
| 51820 | WireGuard (UDP) |

## Memory budget (Pi 4, 1 GB)

Services are capped via `MemoryMax` in systemd units. Avoid adding Postgres or other heavyweight databases — prefer SQLite or embedded storage.
