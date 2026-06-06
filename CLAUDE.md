# Raspi IaC

Agentless infrastructure-as-code for a fleet of Raspberry Pis, using **pyinfra**
(Python, SSH-only, no agents). Two hosts today:

- **raspi** — Pi 4 (1 GB), the full home server (every feature).
- **raspo** — Pi 3 B+, a small **camera node** (base hardening + the `camera`
  vision app + a beszel telemetry agent; no DNS/proxy/apps).

Which tasks run on which host is driven by a **feature map** — see *Hosts &
features* below.

## Deploy

```fish
set -x BW_SESSION (bw unlock --raw)        # unlock Bitwarden first
uv run pyinfra inventory.py deploy.py --limit raspi   # the full server
uv run pyinfra inventory.py deploy.py --limit raspo   # the camera node
```

`--limit <group>` targets one host; omit it to deploy both. `-y` skips the
interactive confirmation (needed for non-interactive runs). A camera-node deploy
needs no `BW_SESSION` (it writes no secrets). Idempotent — safe to re-run.

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
| `deploy.py` | Entry point — includes each task in `DEPLOY` whose feature is in the host's `FEATURES` |
| `group_data/features.py` | Ordered `DEPLOY` manifest (task → feature) + `validate()` for feature sets |
| `inventory.py` | SSH targets, grouped per host (`raspi`, `raspo`) — gitignored |
| `group_data/<host>.py` | Per-host `FEATURES` set (`raspi.py`, `raspo.py`) |
| `group_data/all.py` | All service config (ports, versions, images) — gitignored, shared by all hosts |
| `group_data/all.example.py` | Template for `all.py` — keep in sync when adding services |
| `vault.py` | Bitwarden CLI helpers — secrets fetched at deploy time |
| `tasks/` | One file per service |

## Hosts & features

Multi-host selection is **coarse**: each host picks a set of **features** (task
bundles), not individual services. This avoids making all ~40 tasks individually
host-aware.

- `inventory.py` defines pyinfra **groups** — the variable name *is* the group
  name (`raspi = [...]`, `raspo = [...]`). pyinfra loads `group_data/<group>.py`
  for each, plus `group_data/all.py` for all hosts.
- `group_data/<host>.py` declares `FEATURES` (a set) → `host.data.FEATURES`.
  raspi lists every feature (deploy identical to pre-multi-host); raspo lists
  `{base, camera, telemetry}`.
- `group_data/features.py` holds the ordered `DEPLOY` manifest — `(task_module,
  feature)` tuples preserving execution order (incl. the kanidm OIDC bootstrap
  chain) — plus `FEATURE_DEPS` (hard deps, e.g. `apps`→`containers`) and
  `validate()`. `deploy.py` includes a task only when its feature is in the
  host's `FEATURES`, and **warn-skips** a task whose file doesn't exist yet (so a
  declared-but-unbuilt feature is non-blocking).
- Features: `base` (bootstrap, shell, hardening, network_restrict,
  network_monitor, secrets, host_discover), `dns`, `vpn`, `proxy`, `containers` (podman), `storage`,
  `backup`, `ddns`, `sso`, `monitoring`, `apps`, `chat`, `scribe`, `camera`
  (Pi-camera enable + the `ocular` app), `telemetry` (off-hub beszel-agent).
- `tasks/util.py` `feature(name)` lets a shared `base` task (e.g.
  `tasks/secrets.py`) skip wiring tied to a feature this host lacks. Cross-cutting
  base tasks gate their per-feature blocks with it — so a camera node writes no
  app secrets, opens no app ports, etc.

**Add a feature / host:** add the task(s) to `DEPLOY` with a feature tag (+ a
`FEATURE_DEPS` entry if it has hard deps); add `FEATURES` to the host's
`group_data/<host>.py`; gate any shared base-task blocks with `feature()`.

### The camera node (raspo)

A Pi 3 B+ with the official camera module. Runs `base` + `camera` + `telemetry`:
- `tasks/camera.py` — apt `python3-picamera2` + Pillow + numpy + venv; enables
  `camera_auto_detect`.
- `tasks/ocular.py` — **native deploy** (not a container) of the `ocular`
  camera-vision app from the sibling `../ocular` working tree: build the frontend
  first (`cd ../ocular/frontend && yarn build`), then it ships `dist` + backend
  src to `/opt/ocular`, makes a `--system-site-packages` venv (picamera2 from
  apt, fastapi/uvicorn from pip), renders `/etc/ocular/config.json` from the
  `OCULAR` dict, and runs a sandboxed systemd unit with camera `DeviceAllow` +
  `video` group. raspi's Traefik proxies `ocular.{domain}` to raspo's LAN IP (the
  off-host AI/COMFY pattern), SSO-gated, with a `/status` monitor router.
- `tasks/beszel_agent.py` — native beszel-agent reporting to raspi's hub.

Notes: a fresh camera-node bring-up needs passwordless sudo for the deploy user
(as on raspi). The Pi 3 B+ thrashes on heavy apt installs (the libcamera pull can
hang it — power-cycle + re-run is safe, deploys are idempotent).

## Service patterns

### Native binary (Traefik, wg-portal, Yarr, VuIO, Syncthing)
Use when: single static binary, no container needed.

1. Download binary from GitHub releases, version-stamped to `/usr/local/bin/.{service}-version`
2. Create data dir under `/var/lib/{service}/`
3. Write secrets to `/etc/secrets/{service}.env` (mode 600)
4. Write systemd unit to `/etc/systemd/system/{service}.service` with sandboxing (see below)
5. `systemd.service(running=True, enabled=True, daemon_reload=True)`
6. Hash-based restart detection (stamp file under `/etc/systemd/system/`)

### Podman Quadlet (Vaultwarden, Gatus, ntfy, ABS, Halo, Navidrome, Memos, Kanidm)
Use when: upstream provides a container image.

1. Resolve image tag via `tasks/util.resolve_latest()` if `resolve_latest=True`
2. Create data dir under `/var/lib/{service}/`
3. Write secrets to `/etc/secrets/{service}.env` (mode 600)
4. Write quadlet to `/etc/containers/systemd/{service}.container`
5. Run `/usr/lib/systemd/system-generators/podman-system-generator` to regenerate units
6. `systemd.service(running=True, daemon_reload=True)`
7. Hash-based restart: separate stamps for quadlet hash and env file hash

## Required vs optional services

The deploy is opinionated about which services are core and which are à la carte. Tier matters for *how* a service is wired:

- **Required** — strict `from group_data.all import X`. If someone comments the block out by mistake the deploy fails loud at plan time instead of silently shipping a Pi with no reverse proxy / DNS / SSO / auth gateway. Members: `NETWORK`, `TRAEFIK`, `KANIDM`, `KANIDM_OIDC_CLIENTS`, `KANIDM_PERSONS`, `UNBOUND`, `PIHOLE`, `WIREGUARD`, `OAUTH2_PROXY`, `CIFS`, `HOSTS`, `SHELL`. This is the baseline a fork can ship as-is: networking + DNS + reverse proxy + SSO + hardening, no application services. (Note: "required" means the *dict* is always present in the shared `all.py` so hard imports resolve on every host — it does **not** mean the service's task runs everywhere. Which tasks run is feature-gated per host: the raspo camera node runs none of DNS/proxy/SSO, only `base` + `camera` + `telemetry`.)
- **Optional** — `X = optional("X")` from `tasks.util` plus `if X:` guards. Comment the dict in `group_data/all.py` to retire the service without breaking the deploy. Everything that isn't required is optional: `RESTIC`, `EMAIL`, `HALO` (+ `FMI_PV_FORECAST`), `NAVIDROME`, `VAULTWARDEN`, `MEMOS`, `YARR`, `SYNCTHING`, `VUIO`, `BESZEL`, `CHAT` (+ off-Pi `AI`/`COMFY`/`STT`/`TTS`), `MCP_CHAT`, `TRIVY`, `GATUS`, `NTFY`, `WGPORTAL`, `AUDIOBOOKSHELF`, and the self-hosted-audiobook stack `SCRIBE` + `SHIM` + `SHELF`.
- **Bundles & ripples** — a few optional dicts carry dependencies:
  - **Scribe stack** is all-or-none: comment `SCRIBE`, `SHIM`, `SHELF` together to retire the audiobook app. `SCRIBE` gates `tasks/scribe.py`; the ffmpeg "press" worker is off-Pi (Mac mini) so retiring it is just dropping the press URL.
  - **NTFY is the alert sink.** It degrades gracefully: `tasks/gatus.py` drops its alerting block + per-endpoint alert refs (stays a passive status page), `tasks/restic.py` skips the prune-failure alert, `tasks/trivy.py` keeps scanning but its ntfy pushes no-op, and `tasks/network_monitor.py` (alert-only) stops + disables its timer entirely.
  - **HALO/FMI_PV_FORECAST** — `FMI_PV_FORECAST` is independently optional inside `tasks/halo.py`; retiring `HALO` disables both the dashboard and the PV timer.

### Retiring an optional service

1. Comment the service's dict in `group_data/all.py`.
2. Run the deploy. The task drops into its cleanup branch (stops + disables the systemd unit) and dependent tasks (`tasks/traefik.py`, `tasks/secrets.py`, `tasks/gatus.py`, …) drop the wiring tied to that dict.
3. State on disk (`/var/lib/{service}`, BW item, Kanidm OIDC client, `/etc/secrets/{service}.env`) stays untouched so re-adding the block + redeploying is a clean rollback.

### Making a service retirement-safe

1. **Consumers** — replace every `from group_data.all import X` with `X = optional("X")` and guard module-level uses with `if X:`.
2. **Subdomain registry** — `_SUBDOMAIN_SOURCES` in `group_data/all.py` is built from `_SUBDOMAIN_NAMES` via `globals().get(name)`, so a commented-out dict just drops out; no change needed.
3. **Traefik** — `tasks/traefik.py` is registry-driven: add a `(name, DICT, default_prefix)` tuple to `ROUTES` (the dict is an `optional()` lookup). A route whose dict is `None` is skipped automatically — routers + services for required hosts (pihole, idm, auth) plus the wildcard-cert declaration on the idm router stay put. Only special host shapes (extra monitor routers, oauth2 chains, non-default upstream scheme) need bespoke handling.
4. **Secrets** — gate the service's `/etc/secrets/{service}.env` write in `tasks/secrets.py` behind `if X:` so a retired service stops getting a secret file.
5. **Gatus** — gate the matching endpoint snippet on the dict's presence (see `_halo_endpoint` / `_shelf_endpoint` in `tasks/gatus.py`). Skipping this means gatus alerts on a 404 it caused itself.
6. **The service's own task** — top-level branch on the dict; cleanup branch stops + disables the unit(s), full deploy branch does the usual work. See `tasks/navidrome.py` for the canonical shape.

## Refactoring while adding services

When planning a new service, look for opportunities to clean up existing code that the new service makes awkward — duplicated config keys, repeated patterns that can be looped, hardcoded values that should come from `all.py`. Propose these refactors as part of the plan, not as separate follow-up work.

## Adding a new service — checklist

- [ ] `group_data/all.example.py` — add config dict (host, port, version/image)
- [ ] `group_data/all.py` — mirror the same change verbatim (the file holds no secret values; AI assistants may edit it directly)
- [ ] `vault.py` — add helper function + docstring entry if secrets needed
- [ ] `tasks/{service}.py` — new task file following the pattern above
- [ ] `tasks/traefik.py` — add a `(name, DICT, default_prefix)` tuple to the `ROUTES` registry (DICT resolved via `optional()`); import the dict (if web-accessible). Only special host shapes (extra monitor routers, oauth2 chains, non-http upstream) need bespoke handling beyond the tuple.
- [ ] `group_data/all.py` — append the service's name to `_SUBDOMAIN_NAMES` (if web-accessible). DNS wiring is derived from each dict's optional `"public": True` flag: opt-in lands in `PUBLIC_SUBDOMAINS` (Cloudflare A record + Pi-hole split-DNS), default lands in `INTERNAL_SUBDOMAINS` (Pi-hole only, LAN/VPN clients). Wildcard TLS cert covers both. Be deliberate when adding `public: True` — every public subdomain is a fresh internet-facing attack surface.
- [ ] `tasks/network_restrict.py` — add to `RESTRICTED` list if the service is LAN-only
- [ ] `group_data/all.py` — append `/var/lib/{service}` to `RESTIC["paths"]` if the service has persistent state worth restoring on a blank Pi
- [ ] `group_data/features.py` — add `("{service}", "{feature}")` to `DEPLOY` (this replaces the old `deploy.py` `local.include` line; deploy.py includes by feature). Tag it `apps` for a standard Pi-4 web service, or a new feature if it's a distinct role.
- [ ] Bitwarden — create item in `raspi` folder before deploying

## Secrets handling — AI assistants read this

**Do NOT read secret values into your context.** All live credentials are in
`/etc/secrets/*` on the Pi (env files written by `tasks/secrets.py`) and in
the Bitwarden `raspi` folder. `group_data/all.py` itself contains only
non-secret config plus references to BW field names (e.g. the `secret_env`
dict in `HALO` maps env var → BW field name) — it is safe to read and to edit
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
- `tasks/secrets.py tasks/halo.py` — rotate Halo credentials
- `tasks/secrets.py tasks/traefik.py` — rotate Cloudflare API token
- `tasks/secrets.py tasks/cifs.py` — rotate NAS credentials (remounts shares)

## Security hardening

### Filesystem sandboxing (native binaries)
All native binary services use systemd sandboxing: `ProtectSystem=strict` (read-only root filesystem with explicit `ReadWritePaths`), `ProtectHome=yes`, `PrivateTmp=yes`, `ProtectKernelTunables/Modules/ControlGroups`, `RestrictNamespaces`, `LockPersonality`, and `CapabilityBoundingSet` limited to only what the service needs. A compromised binary can only write to its own data directory.

### Network egress restrictions
LAN-only services (audiobookshelf, beszel-hub, beszel-agent, chat, mcp-chat, navidrome, ntfy, oauth2-proxy, ocular, syncthing, wg-portal, vuio) are blocked from reaching the internet via nftables rules with cgroup-based matching (`tasks/network_restrict.py`). Allowed destinations: localhost, LAN CIDR, WireGuard subnet, SSDP multicast, plus link-local broadcast + `ff12::8384` for Syncthing local discovery. Blocked attempts are logged with `BREACH:<service>:` prefix in the kernel journal, including destination IP. The authoritative list is `RESTRICTED` in `tasks/network_restrict.py` — keep this paragraph in sync when entries change.

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
- Adding a service: add a `(name, DICT, default_prefix)` tuple to the `ROUTES` registry in `tasks/traefik.py` (absent/commented dict → route auto-skipped)

## Backups (restic)

`tasks/restic.py` snapshots service state from `RESTIC["paths"]` to an encrypted repository on the `backups` CIFS share. Daily timer at 03:30 (`raspi-backup.timer`); weekly prune at Sun 04:30 (`raspi-prune.timer`, repo lock declared via `Conflicts=raspi-backup.service` so they cannot overlap, ntfy alert on failure). Repo password lives in the `restic` BW item.

When adding a service with persistent state, append `/var/lib/{service}` to `RESTIC["paths"]` so future blank-slate restores cover it. Add regenerable subdirectories (caches, derived artwork, search indexes) to `RESTIC["excludes"]` to keep snapshots small and avoid overflowing tmpfs `/tmp` during packing.

**Restore-on-blank** runs at deploy time before any service starts. Triggered either interactively (blank Pi + repo present + TTY) or via `RESTORE=true` env var (cold-start case where the NAS share isn't mounted at plan time yet). Idempotent — `/var/lib/.restic-restored` stamp file blocks subsequent restores until removed.

## Ports in use

| Port | Service |
|---|---|
| 80 / 443 | Traefik |
| 3000 | Halo |
| 3001 | Gatus |
| 3002 | Chat |
| 3003 | Scribe |
| 3004 | Scribe shim (loopback) |
| 3006 | Scribe shelf |
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
| 8092 | chat-mcp |
| 8443 | Kanidm (HTTPS) |
| 8096 | VuIO (DLNA) |
| 8099 | ocular (camera node, raspo) |
| 8888 | wg-portal |
| 9090 | oauth2-proxy |
| 13378 | Audiobookshelf |
| 45876 | beszel-agent (off-hub, e.g. raspo) |
| 51820 | WireGuard (UDP) |

## Memory budget (Pi 4, 1 GB)

Services are capped via `MemoryMax` in systemd units. Avoid adding Postgres or other heavyweight databases — prefer SQLite or embedded storage.
