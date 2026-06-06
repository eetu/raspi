"""Per-host data for the `raspo` group (Pi 3 B+ — the camera node).

A deliberately small role: the shared base (bootstrap, hardening, secrets,
egress restriction) plus the `camera` feature (enable the Pi camera + run the
ocular vision app). No second DNS resolver, no app stack, no reverse proxy —
raspi already owns those for the LAN. See group_data/features.py.
"""

FEATURES = {
    "base",
    "camera",
    "telemetry",  # report to raspi's beszel hub via a native beszel-agent
}
