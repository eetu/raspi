"""Declarative reconcile of NetBird account state over its REST API.

Runs LOCALLY (on the control host, not over SSH) against `https://netbird.{domain}`
with the API token tasks/netbird_bootstrap.py put in the vault. Everything NetBird
would otherwise have you click lives here, driven by the NETBIRD dict:

  - API token rotation — NetBird caps token life at 365 days, so a long-lived
    deploy needs to mint its own replacement. Runs first, and the new token is
    used for the rest of the pass.
  - Account settings — PUT every run, declared keys merged over what NetBird has.
  - Groups — POST missing by name.
  - Setup keys — POST missing (or near-expiry) by name. The plaintext key is only
    ever in the POST response, so it is captured into the vault there;
    tasks/secrets.py drops it on the Pi for the agent to enrol with.
  - Network routes — POST missing by network_id, PUT on drift. The routing peer is
    referenced by hostname and resolved against /api/peers.
  - Nameserver groups — POST missing by name, PUT on drift. The nameserver IP is
    the routing peer's *mesh* address, resolved at reconcile time rather than
    pinned, so it self-heals if the peer is ever deleted and re-enrolled.
  - Kanidm federation — POST /api/identity-providers registers Kanidm as a generic
    OIDC connector inside NetBird's embedded IdP. NetBird generates the connector
    id, so the callback URL is only knowable afterwards; it is written back to the
    vault and picked up by tasks/kanidm_oidc.py on the next deploy (the usual
    two-deploy bootstrap).
  - Admin promotion — PUT role=admin for NETBIRD["admin_emails"]. The embedded IdP
    re-issues JWTs and drops upstream role claims, so this cannot be JWT-driven.

Every step skips with an explanation rather than failing the deploy, because on a
first deploy half of this legitimately does not exist yet. Nothing here deletes
state it did not create.

Reachability: `netbird.{domain}` resolves to the LAN IP via Pi-hole for anything on
the LAN, so the control host reaches Traefik directly. Off-LAN (or on a control
host not using Pi-hole) it resolves to the WAN address and depends on the router
hairpinning; the whole task skips with a clear message if it cannot connect.
"""

import json
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta

from pyinfra import logger
from pyinfra.operations import python

import vault
from group_data.all import KANIDM_OIDC_CLIENTS, NETWORK
from tasks.util import optional

NETBIRD = optional("NETBIRD")

# Name of the token this task manages. The one /api/setup issues is used once, to
# mint this; from then on the deploy owns its own credential and can rotate it.
_TOKEN_NAME = "pyinfra-reconcile"
_IDP_NAME = "Kanidm"


def _expires_soon(iso_date: str, within_days: int) -> bool:
    """True if an API-reported expiry is missing, unparseable, or inside the
    renewal window. Unparseable counts as expiring so the credential gets
    replaced rather than silently trusted."""
    if not iso_date:
        return True
    try:
        expiry = datetime.fromisoformat(iso_date.replace("Z", "+00:00"))
    except ValueError:
        return True
    return expiry <= datetime.now(UTC) + timedelta(days=within_days)


def reconcile(state=None, host=None):
    # Read at run time, not import time: on a first deploy the token and the
    # Kanidm client secret are both created earlier in this same deploy.
    pat = vault.netbird_pat()
    if not pat:
        logger.warning(
            "netbird reconcile skipped: no API token in the vault yet. "
            "tasks/netbird_bootstrap.py mints one via POST /api/setup — check its "
            "output above, then re-run the deploy."
        )
        return

    api = f"https://{NETBIRD['url_prefix']}.{NETWORK['domain']}/api"
    renew_days = NETBIRD.get("renew_within_days", 30)

    def request(method, path, body=None):
        req = urllib.request.Request(
            f"{api}{path}",
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={
                "Authorization": f"Token {pat}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    # --- Reachability + token health -------------------------------------
    # A revoked or expired token must not fail the deploy: it should say what to
    # do and let the rest of the run finish.
    try:
        users = request("GET", "/users")
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404):
            logger.warning(
                f"netbird reconcile skipped: the API rejected the stored token ({e.code}). "
                "Delete the `pat` field from the netbird vault item and redeploy to "
                "re-bootstrap, or mint a token in the dashboard and paste it there."
            )
            return
        raise
    except OSError as e:
        logger.warning(
            f"netbird reconcile skipped: cannot reach {api} ({e}). "
            "The control host resolves netbird.{domain} via Pi-hole on the LAN; off-LAN "
            "it needs the router to hairpin. The coordinator itself is unaffected."
        )
        return

    # --- API token rotation ----------------------------------------------
    # Mint a replacement before the 365-day cap bites, and use it for the rest of
    # this pass so a freshly rotated token is proven working immediately.
    owner = next(
        (u for u in users if u.get("email") == NETBIRD["bootstrap_owner_email"]),
        None,
    ) or next((u for u in users if u.get("role") == "owner"), None)

    if owner is None:
        logger.warning("netbird: no owner user found — skipping token rotation")
    else:
        tokens = request("GET", f"/users/{owner['id']}/tokens")
        mine = [t for t in tokens if t.get("name") == _TOKEN_NAME]
        stale = [t for t in mine if _expires_soon(t.get("expiration_date", ""), renew_days)]
        if not mine or stale:
            created = request(
                "POST",
                f"/users/{owner['id']}/tokens",
                {"name": _TOKEN_NAME, "expires_in": 365},
            )
            fresh = created.get("plain_token", "")
            if fresh:
                vault.save_netbird_pat(fresh)
                pat = fresh
                logger.info(f"netbird: minted a fresh '{_TOKEN_NAME}' API token (365 days)")
                # Drop the superseded ones only once the new token is stored, so a
                # crash between the two leaves a working credential behind.
                for old in stale:
                    request("DELETE", f"/users/{owner['id']}/tokens/{old['id']}")
            else:
                logger.warning(
                    "netbird: token rotation returned no plain_token — keeping the old one"
                )

    # --- Account settings -------------------------------------------------
    declared_settings = NETBIRD.get("account_settings", {})
    if declared_settings:
        accounts = request("GET", "/accounts")
        if accounts:
            account = accounts[0]
            current = account.get("settings") or {}

            # A key the server does not echo back can never compare equal, so it
            # would silently re-PUT on every deploy. Name it instead: either the
            # key is misspelled or this NetBird version dropped it.
            unknown = [k for k in declared_settings if k not in current]
            if unknown:
                logger.warning(
                    f"netbird: account_settings keys not recognised by the server and "
                    f"therefore ignored: {sorted(unknown)}. Remove them from "
                    f"NETBIRD['account_settings'] — left in place they force a "
                    f"pointless settings PUT on every deploy."
                )

            drifted = [k for k, v in declared_settings.items() if k in current and current[k] != v]
            if drifted:
                request(
                    "PUT",
                    f"/accounts/{account['id']}",
                    {"settings": {**current, **{k: declared_settings[k] for k in drifted}}},
                )
                logger.info(f"netbird: account settings updated ({', '.join(sorted(drifted))})")

    # --- Groups -----------------------------------------------------------
    existing_names = {g["name"] for g in request("GET", "/groups")}
    for name in NETBIRD.get("groups", []):
        if name not in existing_names:
            request("POST", "/groups", {"name": name})
            logger.info(f"netbird: created group '{name}'")

    # Re-read so newly created groups are resolvable, and pick up NetBird's
    # built-in `All`, which the declarations below reference by name.
    group_id_by_name = {g["name"]: g["id"] for g in request("GET", "/groups")}

    def resolve_groups(names, what):
        ids = sorted(group_id_by_name[n] for n in names if n in group_id_by_name)
        if len(ids) != len(names):
            missing = [n for n in names if n not in group_id_by_name]
            logger.warning(f"netbird: {what} references unknown groups {missing} — skipping")
            return None
        return ids

    # --- Setup keys -------------------------------------------------------
    # The plaintext key exists only in the POST response (GET returns it masked),
    # so it has to be captured here or it is gone.
    existing_keys = request("GET", "/setup-keys")
    for desired in NETBIRD.get("setup_keys", []):
        name = desired["name"]
        matches = [k for k in existing_keys if k.get("name") == name]
        # `valid` is NetBird's own verdict (covers revoked, expired and
        # usage-limit-exhausted); the expiry window is the extra bit it cannot know
        # — replace a key that is still valid but about to lapse.
        usable = [
            k
            for k in matches
            if k.get("valid", True)
            and not k.get("revoked")
            and not _expires_soon(k.get("expires", ""), renew_days)
        ]
        # Also re-create when the vault has no copy: the key is on the server but
        # unusable to us, since NetBird will never show the plaintext again.
        if usable and vault.netbird_setup_key(name):
            continue

        auto_groups = resolve_groups(desired.get("auto_groups", []), f"setup key '{name}'")
        if auto_groups is None:
            continue

        created = request(
            "POST",
            "/setup-keys",
            {
                "name": name,
                "type": desired.get("type", "reusable"),
                "expires_in": desired["expires_in"],
                "usage_limit": desired.get("usage_limit", 0),
                "auto_groups": auto_groups,
                "ephemeral": desired.get("ephemeral", False),
            },
        )
        plaintext = created.get("key", "")
        if plaintext and "*" not in plaintext:
            vault.save_netbird_setup_key(name, plaintext)
            logger.info(f"netbird: created setup key '{name}' and stored it in the vault")
            for old in matches:
                request("DELETE", f"/setup-keys/{old['id']}")
        else:
            logger.warning(
                f"netbird: setup key '{name}' created but the response carried no "
                "plaintext — enrol with a key from the dashboard instead"
            )

    # --- Peers (shared by routes + nameservers) ---------------------------
    peers = request("GET", "/peers")
    peer_by_name = {p["name"]: p for p in peers}

    def resolve_peer(hostname, what):
        peer = peer_by_name.get(hostname)
        if peer is None:
            logger.warning(
                f"netbird: {what} needs peer '{hostname}', which has not enrolled yet — "
                "skipping. Expected on a first deploy: the setup key only reaches "
                "/etc/secrets on the run after it is created, so the peer enrols (and "
                "this resolves) on the next deploy."
            )
        return peer

    # --- Network routes ---------------------------------------------------
    existing_routes = {r["network_id"]: r for r in request("GET", "/routes")}
    for desired in NETBIRD.get("routes", []):
        network_id = desired["network_id"]
        peer = resolve_peer(desired["peer_hostname"], f"route '{network_id}'")
        if peer is None:
            continue
        groups = resolve_groups(desired["groups"], f"route '{network_id}'")
        if groups is None:
            continue

        payload = {
            "network_id": network_id,
            "description": desired.get("description", ""),
            "enabled": desired.get("enabled", True),
            "peer": peer["id"],
            "network": desired["network"],
            "metric": desired.get("metric", 9999),
            "masquerade": desired.get("masquerade", True),
            "groups": groups,
            "skip_auto_apply": desired.get("skip_auto_apply", False),
        }

        current = existing_routes.get(network_id)
        if current is None:
            request("POST", "/routes", payload)
            logger.info(f"netbird: created route '{network_id}' -> {desired['network']}")
            continue

        drifted = (
            current.get("peer") != payload["peer"]
            or current.get("network") != payload["network"]
            or sorted(current.get("groups") or []) != groups
            or current.get("enabled") != payload["enabled"]
            or current.get("masquerade") != payload["masquerade"]
            or current.get("metric") != payload["metric"]
            or (current.get("description") or "") != payload["description"]
            or current.get("skip_auto_apply", False) != payload["skip_auto_apply"]
        )
        if drifted:
            request("PUT", f"/routes/{current['id']}", payload)
            logger.info(f"netbird: updated drifted route '{network_id}'")

    # --- Nameserver groups ------------------------------------------------
    existing_ns = {n["name"]: n for n in request("GET", "/dns/nameservers")}
    for desired in NETBIRD.get("nameservers", []):
        name = desired["name"]
        peer = resolve_peer(desired["peer_hostname"], f"nameserver group '{name}'")
        if peer is None:
            continue
        # The peer's mesh address, assigned by NetBird — this is why the group is
        # reconciled rather than declared with a literal IP.
        mesh_ip = peer.get("ip")
        if not mesh_ip:
            logger.warning(f"netbird: peer '{desired['peer_hostname']}' has no mesh IP yet")
            continue
        groups = resolve_groups(desired["groups"], f"nameserver group '{name}'")
        if groups is None:
            continue

        nameservers = [{"ip": mesh_ip, "ns_type": "udp", "port": desired.get("port", 53)}]
        payload = {
            "name": name,
            "description": desired.get("description", ""),
            "nameservers": nameservers,
            "groups": groups,
            "enabled": desired.get("enabled", True),
            "primary": desired.get("primary", False),
            "domains": desired.get("domains", []),
            "search_domains_enabled": desired.get("search_domains_enabled", False),
        }

        current = existing_ns.get(name)
        if current is None:
            request("POST", "/dns/nameservers", payload)
            logger.info(f"netbird: created nameserver group '{name}' -> {mesh_ip}")
            continue

        drifted = (
            current.get("nameservers") != nameservers
            or sorted(current.get("groups") or []) != groups
            or current.get("enabled") != payload["enabled"]
            or current.get("primary") != payload["primary"]
            or sorted(current.get("domains") or []) != sorted(payload["domains"])
            or current.get("search_domains_enabled") != payload["search_domains_enabled"]
            or (current.get("description") or "") != payload["description"]
        )
        if drifted:
            request("PUT", f"/dns/nameservers/{current['id']}", payload)
            logger.info(f"netbird: updated drifted nameserver group '{name}'")

    # --- Kanidm federation ------------------------------------------------
    # NetBird's embedded IdP brokers Kanidm as an upstream OIDC provider, so VPN
    # login is the same SSO as everything else on the box. Skipped until Kanidm
    # has generated the client secret (deploy 1 of the OIDC bootstrap).
    oidc_client = KANIDM_OIDC_CLIENTS.get("netbird")
    client_secret = vault.kanidm_oidc_secret(oidc_client["secret_field"]) if oidc_client else ""
    if not oidc_client:
        logger.info("netbird: no `netbird` entry in KANIDM_OIDC_CLIENTS — SSO federation skipped")
    elif not client_secret:
        logger.info(
            "netbird: Kanidm has not generated the client secret yet — federation "
            "lands on the next deploy"
        )
    else:
        providers = request("GET", "/identity-providers")
        existing = next((p for p in providers if p.get("name") == _IDP_NAME), None)
        issuer = f"https://idm.{NETWORK['domain']}/oauth2/openid/netbird"
        payload = {
            "type": "oidc",
            "name": _IDP_NAME,
            "issuer": issuer,
            "client_id": "netbird",
            "client_secret": client_secret,
        }
        if existing is None:
            existing = request("POST", "/identity-providers", payload)
            logger.info(f"netbird: registered Kanidm as an OIDC identity provider ({issuer})")
        elif existing.get("issuer") != issuer or existing.get("client_id") != "netbird":
            request("PUT", f"/identity-providers/{existing['id']}", payload)
            logger.info("netbird: updated the drifted Kanidm identity provider")

        # No callback URL is fed back to Kanidm on purpose. The NetBird docs
        # describe a per-connector /oauth2/callback/<connector-id> redirect, but
        # Dex actually authorizes with the bare /oauth2/callback — read straight
        # off a live authorize request. That bare URL is registered statically via
        # KANIDM_OIDC_CLIENTS["netbird"]["redirect_path"], so nothing here depends
        # on the runtime-generated connector id and there is no two-deploy chain.

    # --- Admin promotion --------------------------------------------------
    admin_emails = NETBIRD.get("admin_emails", [])
    if admin_emails:
        admins_group = group_id_by_name.get("admins")
        users = request("GET", "/users")
        for email in admin_emails:
            user = next((u for u in users if u.get("email") == email), None)
            if user is None:
                logger.info(
                    f"netbird: admin {email} not present yet — they need to sign in "
                    "through Kanidm once first"
                )
                continue
            if user.get("role") == "owner":
                continue
            auto_groups = user.get("auto_groups") or []
            wants_group = admins_group is not None and admins_group not in auto_groups
            if user.get("role") == "admin" and not wants_group:
                continue
            request(
                "PUT",
                f"/users/{user['id']}",
                {
                    "role": "admin",
                    "auto_groups": (
                        sorted({*auto_groups, admins_group}) if admins_group else auto_groups
                    ),
                    "is_blocked": user.get("is_blocked", False),
                },
            )
            logger.info(f"netbird: promoted {email} to admin")


if NETBIRD is not None:
    python.call(name="Reconcile NetBird account state", function=reconcile)
