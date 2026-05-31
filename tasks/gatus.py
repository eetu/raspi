"""Gatus: lightweight uptime monitoring + status page (Podman Quadlet).

Optional service — comment the GATUS dict in group_data/all.py to retire
it; the task then stops + disables the `gatus` unit and leaves
/var/lib/gatus on disk for rollback.

Each monitored endpoint is gated on its service's dict, so retiring a
service automatically drops its monitor (no alerting on a 404 Gatus
caused itself). NTFY is the alert sink: when it's retired the alerting
block and every per-endpoint `alerts:` ref are dropped, so Gatus keeps
serving its status page without push notifications.
"""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import (
    CIFS,
    NETWORK,
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
    SHELF = optional("SHELF")
    HALO = optional("HALO")
    NTFY = optional("NTFY")
    VAULTWARDEN = optional("VAULTWARDEN")
    NAVIDROME = optional("NAVIDROME")
    YARR = optional("YARR")
    SYNCTHING = optional("SYNCTHING")
    WGPORTAL = optional("WGPORTAL")

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
    _alerts = "    alerts:\n      - type: ntfy\n" if NTFY else ""
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

    # Per-optional-service endpoint snippets. Each renders as an empty string
    # when its service dict is absent from group_data/all.py.
    _halo_endpoint = (
        f"""  - name: Halo
    url: "https://halo.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if HALO
        else ""
    )
    _audiobookshelf_endpoint = (
        f"""  - name: Audiobookshelf
    url: "https://audiobooks.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if AUDIOBOOKSHELF
        else ""
    )
    _shelf_endpoint = (
        f"""  - name: Shelf
    # /ping is the unauthenticated liveness probe — exercises scribe-shelf
    # without needing the bearer.
    url: "http://{SHELF["host"]}:{SHELF["port"]}/ping"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if SHELF
        else ""
    )
    _wgportal_endpoint = (
        f"""  - name: WireGuard Portal
    url: "https://vpn.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if WGPORTAL
        else ""
    )
    _ntfy_endpoint = (
        f"""  - name: ntfy
    url: "https://ntfy.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if NTFY
        else ""
    )
    _vaultwarden_endpoint = (
        f"""  - name: Vaultwarden
    url: "https://vault.{DOMAIN}"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if VAULTWARDEN
        else ""
    )
    _navidrome_endpoint = (
        f"""  - name: Navidrome
    # Navidrome sits behind oauth2-proxy on the public hostname. Hit it on
    # loopback (gatus uses host networking) so we exercise the unauthenticated
    # OpenSubsonic endpoint without going through SSO.
    url: "http://{NAVIDROME["host"]}:{NAVIDROME["port"]}/rest/getOpenSubsonicExtensions.view?f=json&c=gatus&v=1.16.1"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
      - "[BODY].subsonic-response.status == ok"
{_alerts}
"""
        if NAVIDROME
        else ""
    )
    _yarr_endpoint = (
        f"""  - name: Yarr
    # No public unauth endpoint — accept 200 (authenticated) or 401
    # (oauth2-proxy forwardAuth response). ignore-redirect stops Gatus from
    # following oauth2-proxy's 302 to the Kanidm login page.
    url: "https://rss.{DOMAIN}"
    interval: 1m
    client:
      ignore-redirect: true
    conditions:
      - "[STATUS] == any(200, 302, 401)"
{_alerts}
"""
        if YARR
        else ""
    )
    _syncthing_endpoint = (
        f"""  - name: Syncthing
    # /rest/noauth/health bypasses oauth2-proxy via the syncthing-monitor router.
    url: "https://syncthing.{DOMAIN}/rest/noauth/health"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
"""
        if SYNCTHING
        else ""
    )

    _config_yaml = f"""\
{_alerting}endpoints:
  - name: Pi-hole
    # Public unauth endpoint — bypasses oauth2-proxy via the pihole-monitor router.
    url: "https://pihole.{DOMAIN}/api/info/version"
    interval: 1m
    conditions:
      - "[STATUS] == 200"
{_alerts}
{_halo_endpoint}{_audiobookshelf_endpoint}{_shelf_endpoint}{_wgportal_endpoint}{_ntfy_endpoint}{
        _vaultwarden_endpoint
    }{_navidrome_endpoint}{_yarr_endpoint}{_syncthing_endpoint}  - name: Unbound DNS
    url: "127.0.0.1:{UNBOUND["port"]}"
    interval: 2m
    dns:
      query-name: "pi-hole.net"
      query-type: "A"
    conditions:
      - "len([BODY]) > 0"
{_alerts}
  - name: Pi-hole DNS
    url: "{NETWORK["lan_ip"]}:53"
    interval: 2m
    dns:
      query-name: "pi-hole.net"
      query-type: "A"
    conditions:
      - "len([BODY]) > 0"
{_alerts}
  - name: Pi
    url: "icmp://{NETWORK["lan_ip"]}"
    interval: 1m
    conditions:
      - "[CONNECTED] == true"
{_alerts}
  - name: NAS
    url: "icmp://{_nas_host}"
    interval: 1m
    conditions:
      - "[CONNECTED] == true"
{_alerts}
  - name: Internet
    url: "icmp://1.1.1.1"
    interval: 1m
    conditions:
      - "[CONNECTED] == true"
{_alerts}
storage:
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
