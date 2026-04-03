# Copy this file to inventory.py and fill in your values.

from pyinfra.connectors.ssh import SSHConnector

hosts = [
    (
        SSHConnector,
        {
            "ssh_hostname": "192.168.x.y",  # Pi's LAN IP
            "ssh_user": "your_username",
            "ssh_key": "~/.ssh/your_key",
        },
    ),
]
