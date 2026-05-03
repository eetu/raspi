"""Restic: encrypted incremental backups of service state to the NAS.

Three roles:

  * Daily backup — systemd timer runs /usr/local/sbin/raspi-backup which
    snapshots RESTIC["paths"] into the repo on the `backups` CIFS share and
    enforces retention.
  * Weekly prune — separate timer runs /usr/local/sbin/raspi-prune to
    reclaim space from forgotten snapshots. Kept off the daily path because
    prune is RAM-hungry and locks the repo. ntfy alerts on failure.
  * Restore-on-blank — when an empty Pi is detected at plan time *and* a
    repo exists on the NAS, the user is prompted to restore from the latest
    snapshot before service tasks run, so Vaultwarden DBs / Kanidm state /
    Let's Encrypt certs etc. come up with prior data intact. The same path
    is forced via `RESTORE=true` in the environment for non-interactive or
    cold-start runs where the NAS share isn't yet mounted at plan time.
"""

import io
import os
import subprocess
import sys

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS, NETWORK, NTFY

try:
    from group_data.all import RESTIC
except ImportError:
    RESTIC = None

_BACKUPS = CIFS.get("backups")
if RESTIC is None or _BACKUPS is None:
    # Backups opted out — task body skipped entirely.
    pass
else:
    _REPO_PATH = f"{_BACKUPS['mountpoint']}/raspi-restic"

    def _ssh_probe(cmd: str) -> str:
        try:
            return subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "raspi", cmd],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            ).stdout.strip()
        except Exception:
            return ""

    def _decide_restore() -> bool:
        if os.environ.get("RESTORE", "").lower() in ("1", "true", "yes"):
            print("[restic] RESTORE=true — restore step queued")
            return True
        if not sys.stdin.isatty():
            return False
        # Pi must be a clean slate (no Vaultwarden DB) and the NAS share must
        # already hold a restic repo. On a cold boot the share won't be
        # mounted yet → the env-var override is the way in.
        if _ssh_probe("[ ! -d /var/lib/vaultwarden ] && echo blank") != "blank":
            return False
        if _ssh_probe(f"[ -f {_REPO_PATH}/config ] && echo yes") != "yes":
            return False
        answer = (
            input(
                "[restic] Blank Pi detected with NAS backup available.\n"
                "        Restore /var/lib state from latest snapshot? [y/N] "
            )
            .strip()
            .lower()
        )
        return answer.startswith("y")

    _RESTORE = _decide_restore()

    # --- Install restic binary ---

    server.shell(
        name=f"Install restic {RESTIC['version']}",
        commands=[
            f"""
            INSTALLED=$(/usr/local/bin/restic version 2>/dev/null | awk '{{print $2}}' || true)
            if [ "$INSTALLED" != "{RESTIC["version"]}" ]; then
              curl -fsSL "https://github.com/restic/restic/releases/download/v{RESTIC["version"]}/restic_{RESTIC["version"]}_linux_arm64.bz2" \\
                | bunzip2 > /usr/local/bin/restic
              chmod +x /usr/local/bin/restic
            fi
            """,
        ],
    )

    files.directory(
        name="Create restic cache dir",
        path="/var/cache/restic",
        user="root",
        group="root",
        mode="700",
        present=True,
    )

    # --- Restore step (only when prompted or RESTORE=true) ---

    if _RESTORE:
        server.shell(
            name="Restore from latest restic snapshot",
            commands=[
                f"""
                set -eu
                STAMP=/var/lib/.restic-restored
                if [ -f "$STAMP" ]; then
                  echo "[restic] already restored ($(cat $STAMP)) — skipping"
                  exit 0
                fi
                # Force the CIFS automount to trigger by touching the mountpoint.
                ls {_BACKUPS["mountpoint"]}/ > /dev/null 2>&1 || {{
                  echo "[restic] cannot reach {_BACKUPS["mountpoint"]} — is CIFS mounted?" >&2
                  exit 1
                }}
                if [ ! -f {_REPO_PATH}/config ]; then
                  echo "[restic] no repo at {_REPO_PATH} — nothing to restore" >&2
                  exit 1
                fi
                set -a
                . /etc/secrets/restic.env
                set +a
                /usr/local/bin/restic restore latest --target /
                date -Iseconds > "$STAMP"
                echo "[restic] restore complete"
                """,
            ],
        )

    # --- Daily backup script ---

    _paths_array = "\n".join(f"  {p}" for p in RESTIC["paths"])
    _exclude_flags = " ".join(f"--exclude={p}" for p in RESTIC.get("excludes", []))

    _backup_script = f"""\
#!/usr/bin/env bash
set -euo pipefail

set -a
. /etc/secrets/restic.env
set +a

# Default /tmp is a small tmpfs (~half of 1GB RAM) — restic stages multi-MB
# packs in $TMPDIR before uploading and runs out of space on big initial
# snapshots. Point at a disk-backed location.
export TMPDIR=/var/cache/restic/tmp
mkdir -p "$TMPDIR"

# CIFS automount triggers on first access — force it before restic touches the repo.
ls "$(dirname "$RESTIC_REPOSITORY")/" > /dev/null

if [ ! -f "$RESTIC_REPOSITORY/config" ]; then
  /usr/local/bin/restic init
fi

PATHS=(
{_paths_array}
)

# Skip paths that don't exist yet (service not deployed) so the backup
# doesn't fail on partial setups.
EXISTING=()
for p in "${{PATHS[@]}}"; do
  [ -e "$p" ] && EXISTING+=("$p")
done

/usr/local/bin/restic backup \\
  --tag scheduled \\
  --exclude-caches \\
  {_exclude_flags} \\
  "${{EXISTING[@]}}"

# Retention only — `restic prune` is RAM-hungry on Pi 4 1GB; run that off-host.
/usr/local/bin/restic forget \\
  --keep-daily {RESTIC["retention"]["daily"]} \\
  --keep-weekly {RESTIC["retention"]["weekly"]} \\
  --keep-monthly {RESTIC["retention"]["monthly"]}
"""

    files.put(
        name="Write raspi-backup script",
        src=io.BytesIO(_backup_script.encode()),
        dest="/usr/local/sbin/raspi-backup",
        user="root",
        group="root",
        mode="755",
    )

    _mountpoint_unit = _BACKUPS["mountpoint"].lstrip("/").replace("/", "-")  # mnt-backups

    _service_unit = f"""\
[Unit]
Description=Restic backup of /var/lib service state to NAS
After={_mountpoint_unit}.automount network-online.target
Wants={_mountpoint_unit}.automount network-online.target

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/raspi-backup
Nice=15
IOSchedulingClass=idle
MemoryMax=384M
TimeoutStartSec=2h
"""

    _timer_unit = f"""\
[Unit]
Description=Daily restic backup

[Timer]
OnCalendar={RESTIC["schedule"]}
Persistent=true
RandomizedDelaySec=15min

[Install]
WantedBy=timers.target
"""

    files.put(
        name="Write raspi-backup.service",
        src=io.BytesIO(_service_unit.encode()),
        dest="/etc/systemd/system/raspi-backup.service",
        user="root",
        group="root",
        mode="644",
    )

    files.put(
        name="Write raspi-backup.timer",
        src=io.BytesIO(_timer_unit.encode()),
        dest="/etc/systemd/system/raspi-backup.timer",
        user="root",
        group="root",
        mode="644",
    )

    systemd.service(
        name="Enable raspi-backup.timer",
        service="raspi-backup.timer",
        enabled=True,
        running=True,
        daemon_reload=True,
    )

    # --- Weekly prune ---

    _ntfy_url = f"https://ntfy.{NETWORK['domain']}/{NTFY['topic']}"

    _prune_script = f"""\
#!/usr/bin/env bash
set -euo pipefail

set -a
. /etc/secrets/restic.env
set +a

export TMPDIR=/var/cache/restic/tmp
mkdir -p "$TMPDIR"

ls "$(dirname "$RESTIC_REPOSITORY")/" > /dev/null

NTFY_URL="{_ntfy_url}"

notify_failure() {{
  curl -sf -H "Title: restic prune failed" \\
    -H "Priority: high" \\
    -H "Tags: warning,floppy_disk" \\
    -d "raspi-prune exited non-zero — repo may be growing unchecked. Check journalctl -u raspi-prune." \\
    "$NTFY_URL" > /dev/null || true
}}
trap notify_failure ERR

/usr/local/bin/restic prune --max-unused {RESTIC["prune_max_unused"]}
/usr/local/bin/restic check --read-data-subset=5%
"""

    files.put(
        name="Write raspi-prune script",
        src=io.BytesIO(_prune_script.encode()),
        dest="/usr/local/sbin/raspi-prune",
        user="root",
        group="root",
        mode="755",
    )

    _prune_service_unit = f"""\
[Unit]
Description=Restic prune (reclaim space from forgotten snapshots)
After={_mountpoint_unit}.automount network-online.target
Wants={_mountpoint_unit}.automount network-online.target
# Don't fight the daily backup — they share a repo lock.
Conflicts=raspi-backup.service

[Service]
Type=oneshot
ExecStart=/usr/local/sbin/raspi-prune
Nice=15
IOSchedulingClass=idle
MemoryMax=384M
TimeoutStartSec=4h
"""

    _prune_timer_unit = f"""\
[Unit]
Description=Weekly restic prune

[Timer]
OnCalendar={RESTIC["prune_schedule"]}
Persistent=true
RandomizedDelaySec=30min

[Install]
WantedBy=timers.target
"""

    files.put(
        name="Write raspi-prune.service",
        src=io.BytesIO(_prune_service_unit.encode()),
        dest="/etc/systemd/system/raspi-prune.service",
        user="root",
        group="root",
        mode="644",
    )

    files.put(
        name="Write raspi-prune.timer",
        src=io.BytesIO(_prune_timer_unit.encode()),
        dest="/etc/systemd/system/raspi-prune.timer",
        user="root",
        group="root",
        mode="644",
    )

    systemd.service(
        name="Enable raspi-prune.timer",
        service="raspi-prune.timer",
        enabled=True,
        running=True,
        daemon_reload=True,
    )
