"""Audiobookshelf: Podman Quadlet container unit (arm64-safe)."""

import io

from pyinfra.operations import files, server, systemd

from group_data.all import AUDIOBOOKSHELF, CIFS

quadlet = f"""\
[Unit]
Description=Audiobookshelf
After=network-online.target mnt-audiobooks.automount
Wants=network-online.target mnt-audiobooks.automount

[Container]
Image={AUDIOBOOKSHELF["image"]}
PublishPort={AUDIOBOOKSHELF["host"]}:{AUDIOBOOKSHELF["port"]}:80
Volume={CIFS["mountpoint"]}/OpenAudible/books:/audiobooks:ro
Volume=/var/lib/audiobookshelf/config:/config
Volume=/var/lib/audiobookshelf/metadata:/metadata
Environment=TZ=Europe/Helsinki
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300

[Install]
WantedBy=multi-user.target
"""

files.directory(
    name="Create audiobookshelf config dir",
    path="/var/lib/audiobookshelf/config",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.directory(
    name="Create audiobookshelf metadata dir",
    path="/var/lib/audiobookshelf/metadata",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write audiobookshelf.container quadlet",
    src=io.BytesIO(quadlet.encode()),
    dest="/etc/containers/systemd/audiobookshelf.container",
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
    name="Start Audiobookshelf",
    service="audiobookshelf",
    running=True,
    daemon_reload=True,
)
