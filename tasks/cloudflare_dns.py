"""Create/update Cloudflare DNS A records for all services.
Runs locally (on Mac) during deploy — no remote connection needed.
"""

import json
import urllib.error
import urllib.request

from pyinfra import logger
from pyinfra.operations import python

import secrets as bw
from group_data.all import DOMAIN, NETWORK


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
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    if not result.get("success"):
        raise RuntimeError(f"Cloudflare API error: {result.get('errors')}")
    return result["result"]


def _public_ip():
    with urllib.request.urlopen("https://api.ipify.org") as resp:
        return resp.read().decode().strip()


def _ensure_a_record(subdomain, ip):
    fqdn = f"{subdomain}.{DOMAIN}"
    records = _cf("GET", f"/dns_records?name={fqdn}&type=A")
    payload = {"type": "A", "name": fqdn, "content": ip, "proxied": False, "ttl": 120}

    if records:
        record_id = records[0]["id"]
        if records[0]["content"] == ip:
            logger.info(f"DNS {fqdn} already → {ip}, skipping")
            return
        _cf("PUT", f"/dns_records/{record_id}", payload)
        logger.info(f"DNS updated {fqdn} → {ip}")
    else:
        _cf("POST", "/dns_records", payload)
        logger.info(f"DNS created {fqdn} → {ip}")


def configure_dns(state=None, host=None):
    lan_ip = NETWORK["lan_ip"]
    public_ip = _public_ip()
    logger.info(f"Public IP: {public_ip}")

    for subdomain in ("hcc", "pihole", "abs", "vpn"):
        _ensure_a_record(subdomain, lan_ip)

    _ensure_a_record("wg", public_ip)


python.call(name="Configure Cloudflare DNS records", function=configure_dns)
