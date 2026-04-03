"""CIFS automount: systemd .mount + .automount units for NAS audiobooks share."""

import io

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS

mount_unit = f"""\
[Unit]
Description=Audiobooks NAS share
After=network-online.target
Wants=network-online.target

[Mount]
What={CIFS["share"]}
Where={CIFS["mountpoint"]}
Type=cifs
Options=credentials=/etc/secrets/cifs-audiobooks,vers={CIFS["vers"]},sec={CIFS["sec"]},_netdev,uid=1000,gid=1000,file_mode=0755,dir_mode=0755

[Install]
WantedBy=multi-user.target
"""

automount_unit = f"""\
[Unit]
Description=Audiobooks NAS automount
After=network-online.target
Wants=network-online.target

[Automount]
Where={CIFS["mountpoint"]}
TimeoutIdleSec=0

[Install]
WantedBy=multi-user.target
"""

server.shell(
    name=f"Create mountpoint {CIFS['mountpoint']}",
    commands=[f"mkdir -p {CIFS['mountpoint']}"],
)

files.put(
    name="Write mnt-audiobooks.mount",
    src=io.BytesIO(mount_unit.encode()),
    dest="/etc/systemd/system/mnt-audiobooks.mount",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write mnt-audiobooks.automount",
    src=io.BytesIO(automount_unit.encode()),
    dest="/etc/systemd/system/mnt-audiobooks.automount",
    user="root",
    group="root",
    mode="644",
)

systemd.service(
    name="Enable mnt-audiobooks.automount",
    service="mnt-audiobooks.automount",
    enabled=True,
    running=True,
    daemon_reload=True,
)
