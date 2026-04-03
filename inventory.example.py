# Copy this file to inventory.py and fill in your values.

hosts = [
    (
        "192.168.x.y",  # Pi's LAN IP
        {
            "ssh_user": "your_username",
            "ssh_key": "~/.ssh/your_key",
            "_sudo": True,
        },
    ),
]
