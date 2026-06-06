# Copy this file to inventory.py and fill in your values.
#
# Each top-level list is a pyinfra group; the variable name IS the group name,
# and group_data/<group>.py (e.g. group_data/raspi.py) supplies that group's
# data — notably its FEATURES set, which selects which tasks deploy.py runs.
# group_data/all.py applies to every host. A single-host setup can keep just
# one group.

# Full home server (every feature).
raspi = [
    (
        "192.168.x.y",  # Pi's LAN IP
        {
            "ssh_user": "your_username",
            # Pin the private key so paramiko doesn't iterate every identity.
            "ssh_key": "~/.ssh/your_key",
            "_sudo": True,
        },
    ),
]

# Optional second host with a smaller role (e.g. a camera node). Delete this
# block, and group_data/raspo.py, for a single-host install.
raspo = [
    (
        "192.168.x.z",
        {
            "ssh_user": "your_username",
            "ssh_key": "~/.ssh/your_other_key",
            "_sudo": True,
        },
    ),
]
