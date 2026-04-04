"""Podman: already installed via bootstrap packages.
Enable the API socket (required for Diun and Uptime Kuma container monitoring)
and the auto-update timer.
"""

from pyinfra.operations import systemd

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
)
