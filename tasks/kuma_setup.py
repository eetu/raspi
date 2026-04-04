"""
Seed Uptime Kuma with default monitors, ntfy notification, and status page.

Run once after completing the Uptime Kuma web UI first-run setup:

    uv run pyinfra inventory.py tasks/kuma_setup.py

See docs/status.md for details.
"""

from pyinfra.operations import server

server.shell(
    name="Run kuma-setup.py",
    commands=["python3 /usr/local/bin/kuma-setup.py"],
)
