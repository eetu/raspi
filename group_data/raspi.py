"""Per-host data for the `raspi` group (Pi 4 — the full home server).

FEATURES selects which task bundles deploy.py includes for this host. raspi
runs every feature, so its deploy is identical to the pre-multi-host behavior.
See group_data/features.py for the feature -> task mapping.
"""

FEATURES = {
    "base",
    "containers",
    "dns",
    "vpn",
    "proxy",
    "sso",
    "storage",
    "backup",
    "ddns",
    "monitoring",
    "apps",
    "chat",
    "scribe",
}
