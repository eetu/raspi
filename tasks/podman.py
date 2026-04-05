"""Podman: already installed via bootstrap packages.
Enable the API socket (required for Uptime Kuma container monitoring)
and the auto-update timer. Configure Docker Hub auth to avoid pull rate limits.
"""

import base64
import io
import json

from pyinfra.operations import files, systemd

import vault as bw

_dh = bw.dockerhub_creds()
_auth_token = base64.b64encode(f"{_dh['username']}:{_dh['password']}".encode()).decode()
_auth_json = json.dumps({"auths": {"docker.io": {"auth": _auth_token}}}, indent=2)

_prune_dropin = """\
[Service]
ExecStartPost=podman image prune -f
"""

files.directory(
    name="Create podman-auto-update drop-in dir",
    path="/etc/systemd/system/podman-auto-update.service.d",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write image prune drop-in",
    src=io.BytesIO(_prune_dropin.encode()),
    dest="/etc/systemd/system/podman-auto-update.service.d/prune.conf",
    user="root",
    group="root",
    mode="644",
)

files.directory(
    name="Create /root/.config/containers",
    path="/root/.config/containers",
    user="root",
    group="root",
    mode="700",
    present=True,
)

files.put(
    name="Write Docker Hub auth for Podman",
    src=io.BytesIO(_auth_json.encode()),
    dest="/root/.config/containers/auth.json",
    user="root",
    group="root",
    mode="600",
)

systemd.service(
    name="Enable podman socket",
    service="podman.socket",
    enabled=True,
    running=True,
)

systemd.service(
    name="Enable podman-auto-update timer",
    service="podman-auto-update.timer",
    enabled=True,
    running=True,
    daemon_reload=True,
)
