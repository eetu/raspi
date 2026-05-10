"""Create/update Cloudflare DNS A records for all services.
Runs locally (on Mac) during deploy — no remote connection needed.
"""

import json
import urllib.error
import urllib.request

from pyinfra import logger
from pyinfra.operations import python

import vault as bw
from group_data.all import AI, COMFY, NETWORK, WIREGUARD

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


def configure_dns(state=None, host=None):
    lan_ip = NETWORK["lan_ip"]

    for subdomain in (
        "halo",
        "hcc",  # legacy fallback for halo — keep until clients migrate
        "pihole",
        "audiobooks",
        "vpn",
        "ntfy",
        "status",
        "vault",
        "rss",
        "music",
        "memo",
        "chat",
        "syncthing",
        "metrics",
        "idm",
        "auth",
        AI["url_prefix"],
        COMFY["url_prefix"],
    ):
        _ensure_record(subdomain, lan_ip, "A")

    # wg AAAA record is managed by cloudflare-ddns.sh on the Pi (IPv6 via passthrough)
    if WIREGUARD.get("public_ipv4"):
        public_ip = _public_ip()
        _ensure_record("wg", public_ip, "A")


python.call(name="Configure Cloudflare DNS records", function=configure_dns)
