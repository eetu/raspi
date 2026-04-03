"""CIFS automount: systemd .mount + .automount units for NAS audiobooks share."""

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
Options=credentials=/etc/secrets/cifs-audiobooks,vers={CIFS["vers"]},sec={CIFS["sec"]},_netdev,uid=audiobookshelf,gid=audiobookshelf,iocharset=utf8

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
    src=mount_unit,
    dest="/etc/systemd/system/mnt-audiobooks.mount",
    user="root",
    group="root",
    mode="644",
)

files.put(
    name="Write mnt-audiobooks.automount",
    src=automount_unit,
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
