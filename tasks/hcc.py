"""HCC: Podman Quadlet container unit."""

from pyinfra.operations import files, server, systemd

from group_data.all import HCC

quadlet = f"""\
[Unit]
Description=HCC Dashboard
After=network-online.target
Wants=network-online.target

[Container]
Image={HCC["image"]}
PublishPort={HCC["host"]}:{HCC["port"]}:3000
EnvironmentFile=/etc/secrets/hcc.env
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
    name="Create /etc/containers/systemd",
    path="/etc/containers/systemd",
    user="root",
    group="root",
    mode="755",
    present=True,
)

files.put(
    name="Write hcc.container quadlet",
    src=quadlet,
    dest="/etc/containers/systemd/hcc.container",
    user="root",
    group="root",
    mode="644",
)

server.shell(
    name="Reload quadlet units",
    commands=[
        "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true"
    ],
)

systemd.service(
    name="Enable HCC",
    service="hcc",
    enabled=True,
    running=True,
    daemon_reload=True,
)
