"""Podman: already installed via bootstrap packages.
Enable the auto-update timer that replaces watchtower for HCC.
"""

from pyinfra.operations import systemd

systemd.service(
    name="Enable podman-auto-update timer",
    service="podman-auto-update.timer",
    enabled=True,
    running=True,
)
