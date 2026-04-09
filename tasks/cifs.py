"""CIFS automount: systemd .mount + .automount units for NAS shares."""

import io

from pyinfra.operations import files, systemd

from group_data.all import CIFS

for _name, _share in CIFS.items():
    _unit = _share["mountpoint"].lstrip("/").replace("/", "-")  # e.g. "mnt-audiobooks"

    _mount_unit = f"""\
[Unit]
Description={_name.capitalize()} NAS share
After=network-online.target
Wants=network-online.target

[Mount]
What={_share["share"]}
Where={_share["mountpoint"]}
Type=cifs
Options=credentials=/etc/secrets/cifs-{_name},vers={_share["vers"]},sec={_share["sec"]},_netdev,uid=1000,gid=1000,file_mode=0755,dir_mode=0755

[Install]
WantedBy=multi-user.target
"""

    _automount_unit = f"""\
[Unit]
Description={_name.capitalize()} NAS automount
After=network-online.target
Wants=network-online.target

[Automount]
Where={_share["mountpoint"]}
TimeoutIdleSec=0

[Install]
WantedBy=multi-user.target
"""

    files.directory(
        name=f"Create mountpoint {_share['mountpoint']}",
        path=_share["mountpoint"],
        present=True,
    )

    files.put(
        name=f"Write {_unit}.mount",
        src=io.BytesIO(_mount_unit.encode()),
        dest=f"/etc/systemd/system/{_unit}.mount",
        user="root",
        group="root",
        mode="644",
    )

    files.put(
        name=f"Write {_unit}.automount",
        src=io.BytesIO(_automount_unit.encode()),
        dest=f"/etc/systemd/system/{_unit}.automount",
        user="root",
        group="root",
        mode="644",
    )

    systemd.service(
        name=f"Enable {_unit}.automount",
        service=f"{_unit}.automount",
        enabled=True,
        running=True,
        daemon_reload=True,
    )
