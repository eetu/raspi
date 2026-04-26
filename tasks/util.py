"""Shared utilities for task modules."""

import json
import re
import urllib.request
from collections.abc import Iterable


def restart_if_changed(
    service: str,
    static_hash: str,
    env_files: Iterable[str] = (),
) -> str:
    """Shell command that restarts `service` when its config fingerprint changes.

    `static_hash` covers content known at plan time (unit + inline config strings).
    `env_files` are paths hashed at run time — useful for secrets written by
    tasks/secrets.py that rotate out-of-band. The combined stamp lives at
    `/etc/systemd/system/.{service}-stamp`.
    """
    stamp = f"/etc/systemd/system/.{service}-stamp"
    env_files = tuple(env_files)
    if not env_files:
        return (
            f'if [ "$(cat {stamp} 2>/dev/null)" != "{static_hash}" ]; then\n'
            f"  systemctl restart {service}\n"
            f"  echo '{static_hash}' > {stamp}\n"
            f"fi"
        )
    files_arg = " ".join(env_files)
    return (
        f'CURRENT="{static_hash}"\n'
        f"for f in {files_arg}; do\n"
        f'  CURRENT="$CURRENT:$(sha256sum "$f" | cut -d\' \' -f1)"\n'
        f"done\n"
        f'CURRENT=$(printf "%s" "$CURRENT" | sha256sum | cut -d\' \' -f1)\n'
        f'if [ "$(cat {stamp} 2>/dev/null)" != "$CURRENT" ]; then\n'
        f"  systemctl restart {service}\n"
        f'  echo "$CURRENT" > {stamp}\n'
        f"fi"
    )


def resolve_latest(repo: str, image: str) -> str:
    """Return image with its tag replaced by the latest GitHub release for the same major version.

    Works with both v-prefixed (v5.35.0) and bare (1.33.2) version schemes.
    """
    pinned = image.split(":")[-1].lstrip("v")
    major = pinned.split(".")[0]
    pattern = re.compile(rf"^v?{re.escape(major)}\.\d+\.\d+$")

    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/releases?per_page=10",
        headers={"Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(req) as r:
        releases = json.loads(r.read())

    matching = [r["tag_name"] for r in releases if pattern.match(r["tag_name"])]
    if not matching:
        raise RuntimeError(f"No {repo} releases found for major version {major}")

    original_tag = image.split(":")[-1]
    tag = matching[0]
    if not original_tag.startswith("v"):
        tag = tag.lstrip("v")
    base = image.rsplit(":", 1)[0]
    return f"{base}:{tag}"
