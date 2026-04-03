"""CIFS automount: systemd .mount + .automount units for NAS audiobooks share."""

import io

from pyinfra.operations import files, server, systemd

from group_data.all import CIFS

_nas_hostname = CIFS["share"].split("/")[2]  # extracts "zenwifi" from "//zenwifi/audiobooks"

server.shell(
    name=f"Set /etc/hosts entry for {_nas_hostname}",
    commands=[
        f"""
        ENTRY="{CIFS['host_ip']} {_nas_hostname}"
        if grep -q "\\b{_nas_hostname}\\b" /etc/hosts; then
          sed -i "s/.*\\b{_nas_hostname}\\b.*/$ENTRY/" /etc/hosts
        else
          echo "$ENTRY" >> /etc/hosts
        fi
        """,
    ],
)

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

files.directory(
    name=f"Create mountpoint {CIFS['mountpoint']}",
    path=CIFS["mountpoint"],
    present=True,
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
