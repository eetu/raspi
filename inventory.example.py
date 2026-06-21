# Copy this file to inventory.py and fill in your values.
#
# Each top-level list is a pyinfra group; the variable name IS the group name,
# and group_data/<group>.py (e.g. group_data/raspi.py) supplies that group's
# data — notably its FEATURES set, which selects which tasks deploy.py runs.
# group_data/all.py applies to every host. A single-host setup can keep just
# one group.
#
# SSH auth: pin one key from the SSH agent (e.g. 1Password) per host by its
# comment, so paramiko presents only that key. paramiko has no `IdentitiesOnly`
# equivalent — left alone it offers every agent key one by one, which can trip
# sshd's MaxAuthTries / fail2ban before reaching the right one. Pinning keeps
# the private key in the agent (nothing on the filesystem). Prefer an on-disk
# key file instead? Swap `**_auth(...)` for `"ssh_key": "~/.ssh/your_key"`.

import paramiko


def _agent_key(comment):
    """Return the agent key whose comment matches, or None.

    None on a locked/absent agent or a missing key, so the deploy falls back to
    normal agent behaviour rather than failing at inventory-load time.
    """
    try:
        return next(
            (k for k in paramiko.Agent().get_keys() if getattr(k, "comment", "") == comment),
            None,
        )
    except Exception:
        return None


def _auth(comment):
    key = _agent_key(comment)
    if key:
        return {
            "ssh_paramiko_connect_kwargs": {
                "pkey": key,
                "allow_agent": False,
                "look_for_keys": False,
            }
        }
    return {"ssh_allow_agent": True}


# Full home server (every feature).
raspi = [
    (
        "192.168.x.y",  # Pi's LAN IP
        {
            "ssh_user": "your_username",
            "_sudo": True,
            # The agent-key comment for this host (see `ssh-add -l`).
            **_auth("your-agent-key-comment"),
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
            "_sudo": True,
            **_auth("your-other-agent-key-comment"),
        },
    ),
]
