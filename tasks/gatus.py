"""Gatus: lightweight uptime monitoring + status page (Podman Quadlet).

Optional service — comment the GATUS dict in group_data/all.py to retire
it; the task then stops + disables the `gatus` unit and leaves
/var/lib/gatus on disk for rollback.

Each monitored endpoint is gated on its service's dict, so retiring a
service automatically drops its monitor (no alerting on a 404 Gatus
caused itself). NTFY is the alert sink: when it's retired the alerting
block and every per-endpoint `alerts:` ref are dropped, so Gatus keeps
serving its status page without push notifications.

Probe placement: services fronted by oauth2-proxy are probed on loopback
(gatus uses host networking) — the edge answers 401 before Traefik touches
the upstream, so an edge probe can pass with a dead service behind it.
Services with an unauthenticated public path are probed through Traefik so
the route + wildcard cert are exercised too.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import (
    CIFS,
    NETWORK,
    OAUTH2_PROXY,
    UNBOUND,
)
from tasks.util import optional, resolve_latest

GATUS = optional("GATUS")


if GATUS is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable gatus (kept on disk for rollback)",
        service="gatus",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    # Optional services — comment their dicts in group_data/all.py and the
    # matching endpoint disappears with them.
    AUDIOBOOKSHELF = optional("AUDIOBOOKSHELF")
    BESZEL = optional("BESZEL")
    CHAT = optional("CHAT")
    HALO = optional("HALO")
    MCP_CHAT = optional("MCP_CHAT")
    MEMOS = optional("MEMOS")
    NAVIDROME = optional("NAVIDROME")
    NTFY = optional("NTFY")
    RASPI_DASHBOARD = optional("RASPI_DASHBOARD")
    SCRIBE = optional("SCRIBE")
    SHELF = optional("SHELF")
    SHIM = optional("SHIM")
    SYNCTHING = optional("SYNCTHING")
    VAULTWARDEN = optional("VAULTWARDEN")
    VUIO = optional("VUIO")
    WGPORTAL = optional("WGPORTAL")
    YARR = optional("YARR")

    DOMAIN = NETWORK["domain"]

    _image = (
        resolve_latest("TwiN/gatus", GATUS["image"])
        if GATUS.get("resolve_latest")
        else GATUS["image"]
    )

    # No gatus-side SSO. The server (and its loopback REST API on
    # 127.0.0.1:3001) is left unauthenticated so raspi-dashboard can fan it in;
    # the human-facing gatus.{domain} route is gated by oauth2-proxy at the
    # edge instead (see _gated_hosts in tasks/traefik.py). The gatus Kanidm
    # OIDC client entry is kept in group_data/all.py so DNS/subdomain registration
    # is unaffected — gatus just no longer consumes its secret.

    # NAS healthcheck host — the alias from HOSTS (e.g. "zenwifi"). The container
    # mounts the Pi's /etc/hosts read-only (see quadlet below), so it resolves the
    # alias via the same entry that tasks/host_discover.py keeps fresh.
    _nas_host = CIFS["audiobooks"]["share"].split("/")[2]

    # NTFY is the alert sink. When it's retired, drop the alerting block and
    # every per-endpoint alert ref so Gatus stays a passive status page.
    _alerting = (
        f"""\
alerting:
  ntfy:
    url: "https://ntfy.{DOMAIN}"
    topic: "{NTFY["topic"]}"
    default-alert:
      enabled: true
      failure-threshold: 3
      success-threshold: 1
      send-on-resolved: true

"""
        if NTFY
        else ""
    )

    def _ep(
        name, url, *, group, interval="1m", conditions=("[STATUS] == 200",), comment=None, dns=None
    ):
        """Render one endpoint block, with a per-endpoint ntfy alert ref unless NTFY is retired."""
        lines = [f"  - name: {name}", f"    group: {group}"]
        if comment:
            lines += [f"    # {line}" for line in comment.splitlines()]
        lines.append(f'    url: "{url}"')
        lines.append(f"    interval: {interval}")
        if dns:
            lines.append("    dns:")
            lines += [f'      {key}: "{value}"' for key, value in dns.items()]
        lines.append("    conditions:")
        lines += [f'      - "{condition}"' for condition in conditions]
        if NTFY:
            lines += ["    alerts:", "      - type: ntfy"]
        return "\n".join(lines) + "\n"

    # Required-tier services — always present.
    _endpoints = [
        _ep(
            "Pi-hole",
            f"https://pihole.{DOMAIN}/api/info/version",
            group="dns",
            comment="Public unauth endpoint — bypasses oauth2-proxy via the pihole-monitor router.",
        ),
        _ep(
            "Kanidm",
            f"https://idm.{DOMAIN}/status",
            group="auth",
            comment="Unauthenticated readiness probe, through Traefik so the wildcard cert is exercised too.",
        ),
        _ep(
            "oauth2-proxy",
            f"http://{OAUTH2_PROXY['host']}:{OAUTH2_PROXY['port']}/ping",
            group="auth",
        ),
    ]

    # Optional services — each gated on its dict.
    if HALO:
        _endpoints.append(_ep("Halo", f"https://halo.{DOMAIN}", group="apps"))
    if CHAT:
        _endpoints.append(
            _ep("Chat", f"http://{CHAT['host']}:{CHAT['port']}/healthz", group="apps")
        )
    if MCP_CHAT:
        _endpoints.append(
            _ep("MCP Chat", f"http://{MCP_CHAT['host']}:{MCP_CHAT['port']}/health", group="apps")
        )
    if MEMOS:
        _endpoints.append(
            _ep("Memos", f"http://{MEMOS['host']}:{MEMOS['port']}/healthz", group="apps")
        )
    if AUDIOBOOKSHELF:
        _endpoints.append(_ep("Audiobookshelf", f"https://audiobooks.{DOMAIN}", group="media"))
    if SCRIBE:
        _endpoints.append(
            _ep(
                "Scribe",
                f"http://127.0.0.1:{SCRIBE['port']}",
                group="scribe",
                comment="SCRIBE['host'] is 0.0.0.0 (mini's press worker reaches it directly) — probe loopback.",
            )
        )
    if SHELF:
        _endpoints.append(
            _ep(
                "Shelf",
                f"http://{SHELF['host']}:{SHELF['port']}/ping",
                group="scribe",
                comment=(
                    "/ping is the unauthenticated liveness probe — exercises scribe-shelf\n"
                    "without needing the bearer."
                ),
            )
        )
    if SHIM:
        _endpoints.append(
            _ep(
                "Shim",
                f"http://{SHIM['host']}:{SHIM['port']}/health",
                group="scribe",
                comment="Loopback-only audible sidecar for scribe — /health is unauthenticated.",
            )
        )
    if WGPORTAL:
        _endpoints.append(_ep("WireGuard Portal", f"https://vpn.{DOMAIN}", group="ops"))
    if NTFY:
        _endpoints.append(_ep("ntfy", f"https://ntfy.{DOMAIN}", group="ops"))
    if VAULTWARDEN:
        _endpoints.append(_ep("Vaultwarden", f"https://vault.{DOMAIN}", group="apps"))
    if NAVIDROME:
        _endpoints.append(
            _ep(
                "Navidrome",
                f"http://{NAVIDROME['host']}:{NAVIDROME['port']}/rest/getOpenSubsonicExtensions.view?f=json&c=gatus&v=1.16.1",
                group="media",
                comment=(
                    "Navidrome sits behind oauth2-proxy on the public hostname. Hit the\n"
                    "unauthenticated OpenSubsonic endpoint on loopback to bypass SSO."
                ),
                conditions=("[STATUS] == 200", "[BODY].subsonic-response.status == ok"),
            )
        )
    if YARR:
        _endpoints.append(
            _ep(
                "Yarr",
                f"http://{YARR['host']}:{YARR['port']}",
                group="apps",
                comment=(
                    "Loopback, not https://rss.{domain} — oauth2-proxy answers 401 at the\n"
                    "edge before Traefik touches the upstream, so an edge probe passes\n"
                    "even with yarr dead."
                ),
            )
        )
    if SYNCTHING:
        _endpoints.append(
            _ep(
                "Syncthing",
                f"https://syncthing.{DOMAIN}/rest/noauth/health",
                group="apps",
                comment="/rest/noauth/health bypasses oauth2-proxy via the syncthing-monitor router.",
            )
        )
    if BESZEL:
        _endpoints.append(
            _ep("Beszel", f"http://{BESZEL['host']}:{BESZEL['port']}/api/health", group="ops")
        )
    if VUIO:
        _endpoints.append(
            _ep(
                "VuIO",
                f"http://127.0.0.1:{VUIO['port']}",
                group="media",
                comment="VUIO['host'] is 0.0.0.0 (LAN-wide for DLNA/SSDP) — probe loopback.",
            )
        )
    if RASPI_DASHBOARD:
        _endpoints.append(
            _ep(
                "Dashboard",
                f"http://{RASPI_DASHBOARD['host']}:{RASPI_DASHBOARD['port']}/healthz",
                group="ops",
            )
        )

    # Infra reachability — DNS resolvers + icmp.
    _endpoints += [
        _ep(
            "Unbound DNS",
            f"127.0.0.1:{UNBOUND['port']}",
            group="dns",
            interval="2m",
            dns={"query-name": "pi-hole.net", "query-type": "A"},
            conditions=("len([BODY]) > 0",),
        ),
        _ep(
            "Pi-hole DNS",
            f"{NETWORK['lan_ip']}:53",
            group="dns",
            interval="2m",
            dns={"query-name": "pi-hole.net", "query-type": "A"},
            conditions=("len([BODY]) > 0",),
        ),
        _ep("Pi", f"icmp://{NETWORK['lan_ip']}", group="core", conditions=("[CONNECTED] == true",)),
        _ep("NAS", f"icmp://{_nas_host}", group="core", conditions=("[CONNECTED] == true",)),
        _ep("Internet", "icmp://1.1.1.1", group="core", conditions=("[CONNECTED] == true",)),
    ]

    _config_yaml = f"""\
{_alerting}endpoints:
{"".join(_endpoints)}storage:
  type: sqlite
  path: /data/gatus.db

web:
  address: "{GATUS["host"]}"
  port: {GATUS["port"]}
"""

    quadlet = f"""\
[Unit]
Description=Gatus monitoring
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=gatus
Image={_image}
Network=host
Volume=/etc/gatus/config.yaml:/config/config.yaml:ro
Volume=/var/lib/gatus:/data
Volume=/etc/hosts:/etc/hosts:ro
AddCapability=CAP_NET_RAW

[Service]
Restart=always
RestartSec=10
MemoryMax={GATUS["memory_max"]}

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256((quadlet + _config_yaml).encode()).hexdigest()

    files.directory(
        name="Create /etc/gatus",
        path="/etc/gatus",
        user="root",
        group="root",
        mode="755",
        present=True,
    )

    files.directory(
        name="Create /var/lib/gatus",
        path="/var/lib/gatus",
        user="root",
        group="root",
        mode="755",
        present=True,
    )

    files.put(
        name="Write gatus config.yaml",
        src=io.BytesIO(_config_yaml.encode()),
        dest="/etc/gatus/config.yaml",
        user="root",
        group="root",
        mode="600",
    )

    files.put(
        name="Write gatus.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/gatus.container",
        user="root",
        group="root",
        mode="644",
    )

    server.shell(
        name="Reload quadlet units",
        commands=[
            "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true",
        ],
    )

    systemd.service(
        name="Start gatus",
        service="gatus",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart gatus if config changed",
        commands=[
            f"""
            STAMP=/etc/gatus/.pyinfra-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart gatus
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )
