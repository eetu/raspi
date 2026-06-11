"""Feature manifest — which task modules belong to which feature bundle.

A *feature* is a coarse bundle of task modules. Each host declares the set of
features it runs in its group_data file (group_data/raspi.py,
group_data/raspo.py) as ``FEATURES``; deploy.py walks DEPLOY in order and
includes ``tasks/<module>.py`` only when its feature is in the host's set.

This keeps multi-host selection coarse (pick features, not 40 individual
services) while the existing per-service ``optional()`` machinery in
group_data/all.py still handles retiring one service within a feature.

DEPLOY order is load-bearing and mirrors the historical deploy.py order
exactly — notably kanidm -> kanidm_oidc -> oauth2_proxy/memos/chat/scribe, the
two-deploy OIDC bootstrap chain. Append new tasks in the position they must run.
"""

# (task_module, feature) in execution order.
DEPLOY = [
    ("bootstrap", "base"),
    ("shell", "base"),
    ("hardening", "base"),
    ("network_restrict", "base"),
    ("secrets", "base"),
    ("cloudflare_dns", "dns"),
    ("unbound", "dns"),
    ("pihole", "dns"),
    ("wireguard", "vpn"),
    ("wg_portal", "vpn"),
    ("traefik", "proxy"),
    ("host_discover", "base"),
    ("cifs", "storage"),
    ("restic", "backup"),
    ("podman", "containers"),
    ("halo", "apps"),
    ("audiobookshelf", "apps"),
    ("navidrome", "apps"),
    ("ntfy", "monitoring"),
    ("gatus", "monitoring"),
    ("trivy", "monitoring"),
    ("ddns", "ddns"),
    ("vaultwarden", "apps"),
    ("kanidm", "sso"),
    ("kanidm_oidc", "sso"),
    ("oauth2_proxy", "sso"),
    ("memos", "apps"),
    ("represent", "apps"),
    ("supersaw", "apps"),
    ("chat", "chat"),
    ("mcp_chat", "chat"),
    ("shim", "scribe"),
    ("scribe", "scribe"),
    ("shelf", "scribe"),
    ("yarr", "apps"),
    ("syncthing", "apps"),
    ("vuio", "apps"),
    ("beszel", "monitoring"),
    ("raspi_dashboard", "monitoring"),
    # network_monitor pairs with network_restrict (both base): any host that
    # blocks egress should also alert on BREACH log entries. It no-ops when NTFY
    # is retired. raspo pushes to raspi's ntfy over the LAN (resolves via Pi-hole).
    ("network_monitor", "base"),
    # Off-hub telemetry: report this host to raspi's beszel hub (native agent,
    # no podman). Distinct from the full `monitoring` bundle which runs the hub.
    ("beszel_agent", "telemetry"),
    # Camera node (raspo): enable the Pi camera + run the ocular vision app.
    ("camera", "camera"),
    ("ocular", "camera"),
]

# Every feature that appears in DEPLOY, for membership validation. Keep in sync.
ALL_FEATURES = {feature for _task, feature in DEPLOY}

# Hard dependencies: a feature on the left genuinely won't deploy/function
# without the features on the right (shared infra a task in the bundle needs).
# Advisory soft couplings (e.g. "apps want proxy to be reachable") are NOT
# encoded — only what breaks the deploy itself.
FEATURE_DEPS = {
    "apps": {"containers"},  # all app services are podman quadlets
    "chat": {"containers"},
    "scribe": {"containers"},
    "sso": {"containers"},  # kanidm runs as a container
    "monitoring": {"containers"},  # ntfy/gatus/trivy/beszel/dashboard are containers
    "backup": {"storage"},  # restic writes to the `backups` CIFS share
    "proxy": {"dns"},  # traefik's DNS-01 challenge uses the Cloudflare token
    "camera": {"base"},
    "telemetry": {"base"},  # beszel-agent connects out to the hub on another host
}


def validate(features):
    """Raise on an unknown feature or an unmet hard dependency.

    Called from deploy.py so a typo'd or under-specified FEATURES set fails
    loud at plan time instead of silently shipping a half-wired host.
    """
    features = set(features or ())
    unknown = features - ALL_FEATURES
    if unknown:
        raise ValueError(f"Unknown feature(s) {sorted(unknown)} — valid: {sorted(ALL_FEATURES)}")
    for feat in features:
        missing = FEATURE_DEPS.get(feat, set()) - features
        if missing:
            raise ValueError(
                f"Feature '{feat}' requires {sorted(missing)} — add it to this host's FEATURES"
            )
