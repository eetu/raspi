"""Uptime Kuma: service monitoring dashboard (Podman Quadlet)."""

import hashlib
import io

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS, NETWORK, NTFY, UPTIME_KUMA

quadlet = f"""\
[Unit]
Description=Uptime Kuma monitoring dashboard
After=network-online.target
Wants=network-online.target

[Container]
Image={UPTIME_KUMA["image"]}
Network=host
Volume=/var/lib/uptime-kuma:/app/data
Volume=/run/podman/podman.sock:/var/run/docker.sock:ro
Environment=UPTIME_KUMA_HOST={UPTIME_KUMA["host"]}
Environment=UPTIME_KUMA_PORT={UPTIME_KUMA["port"]}
Environment=UPTIME_KUMA_TRUST_PROXY=1
AddCapability=CAP_NET_RAW
AutoUpdate=registry
HealthCmd=CMD-SHELL curl -sf http://localhost:{UPTIME_KUMA["port"]}/
HealthInterval=30s
HealthTimeout=5s
HealthRetries=3
HealthStartPeriod=30s

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
"""

_quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

files.directory(
    name="Create /var/lib/uptime-kuma",
    path="/var/lib/uptime-kuma",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write uptime-kuma.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/uptime-kuma.container",
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
    name="Start Uptime Kuma",
    service="uptime-kuma",
    running=True,
    daemon_reload=True,
)

# --- Setup script ---
# Deployed to the Pi for one-time seeding after web UI first-run.
# See docs/status.md for usage.

_script_header = f'''\
DB = "/var/lib/uptime-kuma/kuma.db"
DOMAIN = "{NETWORK["domain"]}"
LAN_IP = "{NETWORK["lan_ip"]}"
NAS_IP = "{CIFS["host_ip"]}"
NTFY_URL = "https://ntfy.{NETWORK["domain"]}"
NTFY_TOPIC = "{NTFY["topic"]}"
'''

_script_body = """
import json, os, sqlite3, subprocess, sys
from datetime import datetime, timezone


def main():
    if not os.path.exists(DB):
        sys.exit(f"DB not found at {DB} — complete the web UI first-run setup first.")
    print("Stopping uptime-kuma...")
    subprocess.run(["systemctl", "stop", "uptime-kuma"], check=True)
    try:
        _seed()
    except Exception:
        import traceback; traceback.print_exc()
        sys.exit(1)
    finally:
        print("Starting uptime-kuma...")
        subprocess.run(["systemctl", "start", "uptime-kuma"])


def _seed():
    con = sqlite3.connect(DB)
    cur = con.cursor()

    user = cur.execute("SELECT id FROM user LIMIT 1").fetchone()
    if not user:
        sys.exit("No admin user found — complete web UI first-run setup first.")
    uid = user[0]

    notif_id = _ensure_notification(cur, uid)

    monitor_ids = []
    for name, url in [
        ("HCC",              f"https://hcc.{DOMAIN}"),
        ("Pi-hole",          f"https://pihole.{DOMAIN}/admin"),
        ("Audiobookshelf",   f"https://abs.{DOMAIN}"),
        ("WireGuard portal", f"https://vpn.{DOMAIN}"),
        ("ntfy",             f"https://ntfy.{DOMAIN}"),
    ]:
        monitor_ids.append(_ensure_http(cur, uid, name, url))

    for name, host in [("Pi", LAN_IP), ("NAS", NAS_IP)]:
        monitor_ids.append(_ensure_ping(cur, uid, name, host))

    monitor_ids.append(_ensure_dns(cur, uid))

    monitor_ids.append(_ensure_ping(cur, uid, "Internet", "1.1.1.1"))

    docker_host_id = _ensure_docker_host(cur, uid)
    for container in ["hcc", "audiobookshelf", "ntfy", "uptime-kuma"]:
        monitor_ids.append(_ensure_docker_monitor(cur, uid, docker_host_id, container))

    for mid in monitor_ids:
        _get_or_insert(
            cur,
            "SELECT id FROM monitor_notification WHERE monitor_id=? AND notification_id=?",
            (mid, notif_id),
            "INSERT INTO monitor_notification (monitor_id, notification_id) VALUES (?,?)",
            (mid, notif_id),
        )
    print(f"  Linked {len(monitor_ids)} monitors to ntfy notification")

    page_id  = _ensure_status_page(cur)
    group_id = _ensure_group(cur, page_id)
    for i, mid in enumerate(monitor_ids):
        _get_or_insert(
            cur,
            "SELECT id FROM monitor_group WHERE monitor_id=? AND group_id=?",
            (mid, group_id),
            "INSERT INTO monitor_group (monitor_id, group_id, weight) VALUES (?,?,?)",
            (mid, group_id, (i + 1) * 100),
        )
    print(f"  Linked {len(monitor_ids)} monitors to status page group")

    con.commit()
    con.close()
    print("Setup complete.")


def _get_or_insert(cur, sel, sel_p, ins, ins_p):
    row = cur.execute(sel, sel_p).fetchone()
    if row:
        return row[0]
    cur.execute(ins, ins_p)
    return cur.lastrowid


def _ensure_notification(cur, uid):
    row = cur.execute("SELECT id FROM notification WHERE name=\'ntfy\'").fetchone()
    if row:
        print(f"  ntfy notification already exists (id={row[0]})")
        return row[0]
    # type is stored inside config JSON, not as a separate column
    cfg = json.dumps({
        "type": "ntfy",
        "ntfyserverurl": NTFY_URL,
        "ntfytopic": NTFY_TOPIC,
        "ntfyAuthenticationMethod": "none",
        "ntfyPriority": 4,
        "ntfyPriorityDown": 5,
    })
    cur.execute(
        "INSERT INTO notification (name, active, user_id, is_default, config) VALUES (?,1,?,1,?)",
        ("ntfy", uid, cfg),
    )
    nid = cur.lastrowid
    print(f"  + ntfy notification (id={nid})")
    return nid


def _ensure_http(cur, uid, name, url):
    row = cur.execute("SELECT id FROM monitor WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO monitor (name, type, url, interval, maxretries, retry_interval, active, user_id)"
        " VALUES (?,\'http\',?,60,3,20,1,?)",
        (name, url, uid),
    )
    mid = cur.lastrowid
    print(f"  + {name} (HTTP)")
    return mid


def _ensure_ping(cur, uid, name, hostname):
    row = cur.execute("SELECT id FROM monitor WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO monitor (name, type, hostname, interval, maxretries, retry_interval, active, user_id)"
        " VALUES (?,\'ping\',?,60,3,20,1,?)",
        (name, hostname, uid),
    )
    mid = cur.lastrowid
    print(f"  + {name} (Ping)")
    return mid


def _ensure_dns(cur, uid):
    name = "Pi-hole DNS"
    row = cur.execute("SELECT id FROM monitor WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO monitor (name, type, hostname, dns_resolve_server, dns_resolve_type, port, interval, maxretries, retry_interval, active, user_id)"
        " VALUES (?,\'dns\',\'pi-hole.net\',?,\'A\',53,120,3,20,1,?)",
        (name, LAN_IP, uid),
    )
    mid = cur.lastrowid
    print(f"  + {name} (DNS)")
    return mid


def _ensure_status_page(cur):
    row = cur.execute("SELECT id FROM status_page WHERE slug=\'home\'").fetchone()
    if row:
        print(f"  status page \'home\' already exists (id={row[0]})")
        return row[0]
    cur.execute(
        "INSERT INTO status_page (slug, title, description, icon, theme, published)"
        " VALUES (\'home\', \'Pi Status\', \'Raspberry Pi home server\', \'\', \'dark\', 1)",
    )
    pid = cur.lastrowid
    print(f"  + status page \'home\' (id={pid})")
    return pid


def _ensure_docker_host(cur, uid):
    row = cur.execute("SELECT id FROM docker_host WHERE name=\'Podman\'").fetchone()
    if row:
        print(f"  docker host \'Podman\' already exists (id={row[0]})")
        return row[0]
    cur.execute(
        "INSERT INTO docker_host (user_id, docker_daemon, docker_type, name)"
        " VALUES (?, \'/var/run/docker.sock\', \'socket\', \'Podman\')",
        (uid,),
    )
    hid = cur.lastrowid
    print(f"  + docker host \'Podman\' (id={hid})")
    return hid


def _ensure_docker_monitor(cur, uid, docker_host_id, container):
    name = f"Container: {container}"
    row = cur.execute("SELECT id FROM monitor WHERE name=?", (name,)).fetchone()
    if row:
        return row[0]
    cur.execute(
        "INSERT INTO monitor (name, type, docker_container, docker_host, interval, maxretries, retry_interval, active, user_id)"
        " VALUES (?,\'docker\',?,?,60,3,20,1,?)",
        (name, f"systemd-{container}", docker_host_id, uid),
    )
    mid = cur.lastrowid
    print(f"  + {name} (Docker)")
    return mid


def _ensure_group(cur, page_id):
    row = cur.execute("SELECT id FROM \'group\' WHERE status_page_id=? AND name=\'Services\'", (page_id,)).fetchone()
    if row:
        print(f"  group \'Services\' already exists (id={row[0]})")
        return row[0]
    cur.execute(
        "INSERT INTO \'group\' (name, public, active, weight, status_page_id)"
        " VALUES (\'Services\', 0, 1, 1000, ?)",
        (page_id,),
    )
    gid = cur.lastrowid
    print(f"  + group \'Services\' (id={gid})")
    return gid


if __name__ == "__main__":
    main()
"""

_setup_script = "#!/usr/bin/env python3\n" + _script_header + _script_body

files.put(
    name="Write kuma-setup.py",
    src=io.BytesIO(_setup_script.encode()),
    dest="/usr/local/bin/kuma-setup.py",
    user="root",
    group="root",
    mode="700",
)

server.shell(
    name="Restart Uptime Kuma if quadlet changed",
    commands=[
        f"""
        STAMP=/var/lib/uptime-kuma/.pyinfra-stamp
        if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
          systemctl restart uptime-kuma
          echo '{_quadlet_hash}' > "$STAMP"
        fi
        """,
    ],
)
