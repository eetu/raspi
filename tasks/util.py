"""Shared utilities for task modules."""

import json
import re
import urllib.request


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

    base = image.rsplit(":", 1)[0]
    return f"{base}:{matching[0]}"
