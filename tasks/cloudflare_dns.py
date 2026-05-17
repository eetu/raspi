"""Create/update Cloudflare DNS A records for all services.
Runs locally (on Mac) during deploy — no remote connection needed.
"""

import json
import urllib.error
import urllib.request

from pyinfra import logger
from pyinfra.operations import python

import vault as bw
from group_data.all import NETWORK, PUBLIC_SUBDOMAINS, WIREGUARD

DOMAIN = NETWORK["domain"]


def _cf(method, path, data=None):
    token = bw.cloudflare()["token"]
    zone_id = bw.cloudflare()["zone_id"]
    url = f"https://api.cloudflare.com/client/v4/zones/{zone_id}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        result = json.loads(resp.read())
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare API error: {result.get('errors')}")
    return result["result"]


def _public_ip():
    for url in (
        "https://api4.ipify.org",
        "https://ipv4.icanhazip.com",
        "https://ipv4.wtfismyip.com/text",
    ):
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                return resp.read().decode().strip()
        except Exception:
            continue
    raise RuntimeError("Could not determine public IP from any service")


def _ensure_record(subdomain, ip, rtype):
    fqdn = f"{subdomain}.{DOMAIN}"
    records = _cf("GET", f"/dns_records?name={fqdn}&type={rtype}")
    payload = {"type": rtype, "name": fqdn, "content": ip, "proxied": False, "ttl": 120}

    if records:
        record_id = records[0]["id"]
        if records[0]["content"] == ip:
            logger.info(f"DNS {fqdn} {rtype} already → {ip}, skipping")
            return
        _cf("PUT", f"/dns_records/{record_id}", payload)
        logger.info(f"DNS updated {fqdn} {rtype} → {ip}")
    else:
        _cf("POST", "/dns_records", payload)
        logger.info(f"DNS created {fqdn} {rtype} → {ip}")


def _reap_orphan_records(lan_ip, protected):
    # Delete A records that point at our LAN IP but aren't in PUBLIC_SUBDOMAINS
    # (or wg, owned by tasks/ddns.py). These accumulate when a service flips
    # from public → internal and its old public A record would otherwise leak
    # the LAN IP + service inventory via DNS enumeration. Scope is intentionally
    # narrow: same `content` as what this task creates, only A records, never
    # the apex.
    suffix = f".{DOMAIN}"
    records = _cf("GET", f"/dns_records?type=A&content={lan_ip}&per_page=100")
    for rec in records:
        name = rec["name"]
        if not name.endswith(suffix) or name == DOMAIN:
            continue
        sub = name[: -len(suffix)]
        if sub in protected:
            continue
        _cf("DELETE", f"/dns_records/{rec['id']}")
        logger.info(f"DNS reaped {name} A (not in PUBLIC_SUBDOMAINS)")


def configure_dns(state=None, host=None):
    lan_ip = NETWORK["lan_ip"]

    # Internal-only subdomains (everything not flagged `public=True`) are
    # excluded — Pi-hole still resolves them for LAN/VPN clients via
    # /etc/pihole/custom.list.
    for subdomain in PUBLIC_SUBDOMAINS:
        _ensure_record(subdomain, lan_ip, "A")

    # wg AAAA record is managed by cloudflare-ddns.sh on the Pi (IPv6 via passthrough)
    if WIREGUARD.get("public_ipv4"):
        public_ip = _public_ip()
        _ensure_record("wg", public_ip, "A")

    _reap_orphan_records(lan_ip, set(PUBLIC_SUBDOMAINS) | {"wg"})


python.call(name="Configure Cloudflare DNS records", function=configure_dns)
