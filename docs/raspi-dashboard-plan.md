# raspi-dashboard — implementation plan

Own Rust + SPA app that surfaces gatus health, beszel metrics, and an
on-demand "scan CVEs" button on one screen. Own image (`ghcr.io/eetu/raspi-dashboard`),
wired into the stack like scribe/chat.

## Goal

One LAN-only page that answers "is everything ok, and is anything vulnerable?"
without three separate logins:

- live service health (from gatus)
- host + container metrics (from beszel)
- press-to-scan CVE report (trivy, run out-of-band)

## Data flow

```
browser ──https──▶ traefik (oauth2-proxy gate) ──▶ dashboard backend :PORT (loopback)
                                                      │
            poll  ┌──────────────────────────────────┼───────────────────────────┐
                  ▼ (REST, loopback, unauth)          ▼ (PB token)                 ▼ (file handshake)
              gatus :3001                         beszel :8091                trivy-cve-scan.service
           /api/v1/.../statuses              PocketBase REST + SSE           writes last-scan.json
                                                                              triggered via .path unit
```

Backend fans these three in, normalizes, and pushes to the SPA over its own SSE.

## Service-to-service auth — the core problem

The dashboard backend is a machine, not a human — it can't do interactive OIDC.
Three different upstreams → three different mechanisms:

### 1. gatus — make the loopback API auth-free, gate humans at the edge

gatus's `security.oidc` (if set) gates its **entire** web server, API included, behind
an interactive session — unusable service-to-service. Recommended fix:

- **Drop gatus's native OIDC** (`security:` block + the `gatus` Kanidm client).
- Gate the public `gatus.{domain}` route with **oauth2-proxy forward-auth** instead
  (same pattern as pihole/rss/syncthing — add `gatus` to `OAUTH2_GATED_HOSTS`).
- Result: gatus API is open on `127.0.0.1:3001`, the dashboard reads it freely; humans
  still need Kanidm via oauth2-proxy.
- Subdomain ownership moves from the OIDC client to the `GATUS` dict: add
  `"url_prefix": "gatus", "public": True` to `GATUS` and add `"GATUS"` to
  `_SUBDOMAIN_NAMES`.

Alternative (no gatus change): mount `/var/lib/gatus/gatus.db` read-only into the
dashboard and query it directly. Zero auth, but couples to gatus's SQLite schema.
**Lean API + oauth2-proxy** — schema is less stable than the documented API.

### 2. beszel — PocketBase service account (machine credential)

Beszel hub is a PocketBase app; PB always requires a token (no loopback bypass).

- Create a dedicated **read-only PB user** `dashboard@…` (not the human OIDC login).
- Store its creds in `/etc/secrets/raspi-dashboard.env`; backend does
  `POST /api/collections/users/auth-with-password` → token (cache + refresh on 401).
- Read: `GET /api/collections/{systems|system_stats|container_stats}/records`.
- Live: subscribe `/api/realtime` (PB SSE) → relay to SPA. (Poll-first in v1, realtime v2.)
- Provision the service user via PB superuser API on deploy (memos-style REST bootstrap
  in `tasks/raspi_dashboard.py`), or once by hand.

### 3. trivy — filesystem handshake, no auth, no in-container privilege

trivy peaks ~310 MB — must NOT run inside the dashboard's cgroup. The container also
shouldn't hold privilege to drive host systemd. Decouple via a path unit:

- `trivy-cve-scan.service` gains a step that writes the report to
  `/var/lib/trivy/last-scan.json` (`--format json`, alongside/instead of the ntfy push).
- Dashboard reads that file for display (RO mount of `/var/lib/trivy`).
- Button press: backend `touch`es `/var/lib/trivy/scan-request` (shared writable mount).
- New host unit `trivy-cve-scan.path` (`PathChanged=/var/lib/trivy/scan-request`) →
  starts `trivy-cve-scan.service`. RAM spike stays in trivy's own unit + MemoryMax.
- Backend polls `last-scan.json` mtime to know when the run finished.

### 4. dashboard itself — human auth at the edge

Gate `dashboard.{domain}` with **oauth2-proxy forward-auth** (add to `OAUTH2_GATED_HOSTS`).
No dashboard Kanidm OIDC client needed — simplest, consistent. SPA↔backend is same-origin
behind the gate; backend↔upstreams use mechanisms 1-3.

## Phase 0 — subdomain rename (independent, ship first)

`status → gatus`, `metrics → beszel` so the host name names the service.

Where the names live + ripple:
- `KANIDM_OIDC_CLIENTS["gatus"].url_prefix`: `status → gatus` (or remove client entirely
  per §1). `["beszel"].url_prefix`: `metrics → beszel`.
- `tasks/traefik.py` `ROUTES`: rename default prefixes `status→gatus`, `metrics→beszel`
  (router/service names can stay; only the `Host()` subdomain changes).
- `PUBLIC_SUBDOMAINS` recomputes automatically → Cloudflare A record + Pi-hole split-DNS
  follow on next deploy (`cloudflare_dns.py`, `pihole.py`). Wildcard cert already covers both.
- Service redirect config: beszel OIDC redirect `https://metrics.→beszel`; gatus
  `redirect-url` (if keeping its OIDC — but §1 drops it).
- **Gotcha:** `kanidm_oidc.py` PATCHes only `oauth2_rs_origin` (redirect) on existing
  clients, not `oauth2_rs_origin_landing`. After a rename the landing origin stays stale.
  Fix once: extend the PATCH (line ~201) to also set
  `"oauth2_rs_origin_landing": [cfg["origin"]]` — then renames fully self-apply with no
  secret churn. (Delete+recreate also works but regenerates the secret → 2-deploy propagate.)
- Stale DNS: old `status`/`metrics` Cloudflare A records won't be auto-deleted — prune by hand.
- Existing bookmarks/clients break — acceptable (personal).

This phase is shippable alone and de-risks the dashboard work.

## Phase 1 — trivy JSON-to-disk + path trigger

- `tasks/trivy.py`: cve-scan writes `/var/lib/trivy/last-scan.json` (structured: per-image
  `{image, critical, high, vulns[]}` + `scanned_at`). Keep `notify()` (now optional digest).
- Add `trivy-cve-scan.path` unit watching `/var/lib/trivy/scan-request`.
- Add `/var/lib/trivy` to `RESTIC["excludes"]` (regenerable) — already partly excluded.

## Phase 2 — dashboard app (own repo/image)

Rust (axum) backend + embedded SPA (same shape as scribe/chat).

Backend endpoints (all behind oauth2-proxy via traefik):
- `GET /api/health` → normalized gatus statuses (name, group, up, uptime, last results).
- `GET /api/metrics` → beszel systems + latest stats (cpu, mem, disk, net, containers).
- `GET /api/cve` → parsed `last-scan.json` (+ `scanned_at`, staleness).
- `POST /api/cve/scan` → touch `scan-request`, return 202; client polls `/api/cve`.
- `GET /api/stream` → SSE: backend multiplexes gatus poll + beszel realtime → live UI.

Data shapes (verify against live APIs at build):
- gatus: `[{key,name,group,results:[{status,success,timestamp,duration,conditionResults}],uptime}]`
- beszel (PB record): `systems` `{name, host, status, info:{cpu,mem,disk,...}}`; `container_stats` per container.
- scan: `{scanned_at, images:[{image, critical, high, vulns:[{id,pkg,severity,title}]}]}`

SPA: status grid (green/red + uptime), metrics cards/sparklines, CVE panel with "Scan now"
+ last-scan timestamp + per-image findings.

## Phase 3 — IaC wiring (`tasks/raspi_dashboard.py`)

Copy the scribe/chat quadlet pattern:
- `RASPI_DASHBOARD` dict in `all.py` + `all.example.py`: host `127.0.0.1`, port (new, e.g.
  3007), `url_prefix: "dashboard"`, image, `public: False` (LAN-only).
- Quadlet, `Network=host`, bind loopback, `MemoryMax≈96M` (backend tiny; scan is external),
  `MALLOC_ARENA_MAX=2` in `[Container]`.
- Mounts: `/var/lib/trivy:ro` (read scan) + a writable spot for `scan-request`
  (or a tiny tmpfs/`/run` path both see), `/etc/secrets/raspi-dashboard.env`.
- `optional()` + cleanup branch (retirement-safe from day one).
- `tasks/traefik.py`: add `("dashboard", RASPI_DASHBOARD, "dashboard")` to `ROUTES`;
  add `dashboard` to `OAUTH2_GATED_HOSTS`.
- `tasks/secrets.py`: write `/etc/secrets/raspi-dashboard.env` (beszel PB service creds)
  gated on the dict.
- `all.py`: add `dashboard` to `_SUBDOMAIN_NAMES`; `/var/lib/raspi-dashboard` to
  `RESTIC["paths"]` only if it stores state (probably stateless → skip).
- `tasks/network_restrict.py`: add `raspi-dashboard` to `RESTRICTED` (LAN-only).
- `deploy.py`: `local.include` after kanidm_oidc + beszel + trivy.
- BW: create `raspi-dashboard` item (beszel service-user creds) before deploy.

## Deploy / bootstrap order

1. Phase 0 rename + kanidm_oidc landing-origin fix → deploy → verify gatus/beszel reachable
   at new subdomains, OIDC still logs in.
2. Phase 1 trivy JSON+path → deploy → confirm scan-request triggers a run + writes JSON.
3. Build the app image (own repo, CI → ghcr).
4. Create BW `raspi-dashboard` item; provision beszel PB service user.
5. Phase 3 wiring → deploy → dashboard at `dashboard.{domain}`.

## Open questions — verify at build time

- Does gatus serve `/api/v1/.../statuses` unauthenticated when `security` is unset? (Expected
  yes.) Confirms §1.
- beszel/PB: exact collection names + record shapes on this version; does the read-only role
  suffice for `system_stats`/`container_stats`? Realtime SSE auth handshake specifics.
- Can the unprivileged dashboard container write `scan-request` to a path the host `.path`
  unit watches? (Shared bind under `/var/lib/trivy` owned appropriately, or `/run` tmpfs.)
- Memory: confirm backend idle RSS fits 96M with `MALLOC_ARENA_MAX=2`; trivy stays external.

## Memory budget note (Pi 4, 1 GB)

Backend is a tiny Rust service (~10-20 MB). The only spike is trivy (~310 MB), which runs in
its **own** unit out-of-band — never in the dashboard's cgroup. Net new steady-state cost is
small; this is the cheap way to get a CVE button without a resident scanner.
