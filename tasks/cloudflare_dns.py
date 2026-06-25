"""Create/update Cloudflare DNS records for all services + inbound email.
Runs locally (on Mac) during deploy — no remote connection needed.
"""

import json
import urllib.error
import urllib.request

from pyinfra import logger
from pyinfra.operations import python

import vault
from group_data.all import NETWORK, PUBLIC_SUBDOMAINS, WIREGUARD
from tasks.util import optional

DOMAIN = NETWORK["domain"]
EMAIL = optional("EMAIL")


def _cf(method, path, data=None):
    token = vault.cloudflare()["token"]
    zone_id = vault.cloudflare()["zone_id"]
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


def _txt_unwrap(s):
    """Strip the surrounding double-quotes Cloudflare uses on TXT content.

    Cloudflare's dashboard requires TXT values be sent quoted; it returns them
    quoted too, and joins multi-chunk values (`"part1" "part2"`) with `" "` —
    normalize that back to one logical string so prefix matches work."""
    s = s.strip()
    if len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        return s[1:-1].replace('" "', "")
    return s


def _upsert(name, rtype, content, *, priority=None, match_prefix=None):
    """Idempotent create-or-update for one DNS record.

    Matching strategy depends on record type:
      - A / CNAME: unique on (name, type) — match the single existing record.
      - MX: multiple records share (name, type), distinguished by content (host).
        Match on (name, type, content); update priority if it drifts.
      - TXT: multiple records share (name, type) — apex has SPF + provider
        verification side by side. `match_prefix` (e.g. "v=spf1") picks the
        right one; without it, falls back to first-record matching. TXT
        content is sent wrapped in double quotes (Cloudflare requirement) and
        unwrapped for comparison so we don't false-mismatch quoted records.

    Records are written unproxied — Cloudflare's HTTP proxy breaks MX/TXT and
    is irrelevant for A records that point at a LAN IP.
    """
    records = _cf("GET", f"/dns_records?name={name}&type={rtype}")

    if rtype == "TXT":
        norm = _txt_unwrap(content)
        api_content = f'"{norm}"'
    else:
        norm = content
        api_content = content

    def _content_of(rec):
        return _txt_unwrap(rec["content"]) if rtype == "TXT" else rec["content"]

    if rtype == "MX":
        existing = next((r for r in records if r["content"] == norm), None)
    elif match_prefix is not None:
        existing = next((r for r in records if _content_of(r).startswith(match_prefix)), None)
    else:
        existing = records[0] if records else None

    payload = {"type": rtype, "name": name, "content": api_content, "proxied": False, "ttl": 120}
    if priority is not None:
        payload["priority"] = priority

    desc = f"{name} {rtype}"
    if priority is not None:
        desc += f" pri={priority}"
    desc += f" → {norm}"

    if existing:
        # Compare raw stored value against what we'd send. For TXT this catches
        # the quoted-vs-unquoted migration (Cloudflare stores TXT exactly as
        # received; sending the value re-quoted forces a one-time rewrite).
        same_content = existing["content"] == api_content
        same_priority = priority is None or existing.get("priority") == priority
        if same_content and same_priority:
            logger.info(f"DNS {desc} already set, skipping")
            return
        _cf("PUT", f"/dns_records/{existing['id']}", payload)
        logger.info(f"DNS updated {desc}")
    else:
        _cf("POST", "/dns_records", payload)
        logger.info(f"DNS created {desc}")


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


def _configure_email_dns():
    # Apex provider-verification TXT (e.g. "protonmail-verification=..."). Matched
    # by exact content so it never collides with the SPF TXT sitting next to it.
    verify = EMAIL.get("verification_txt")
    if verify:
        # Use the value's "key=" prefix as the discriminator — that's what
        # makes the verification record unique among other apex TXT records.
        prefix = verify.split("=", 1)[0] + "=" if "=" in verify else verify
        _upsert(DOMAIN, "TXT", verify, match_prefix=prefix)

    for host, priority in EMAIL.get("mx") or []:
        _upsert(DOMAIN, "MX", host, priority=priority)

    spf = EMAIL.get("spf")
    if spf:
        _upsert(DOMAIN, "TXT", spf, match_prefix="v=spf1")

    for selector, target in (EMAIL.get("dkim") or {}).items():
        _upsert(f"{selector}.{DOMAIN}", "CNAME", target)

    dmarc = EMAIL.get("dmarc")
    if dmarc:
        _upsert(f"_dmarc.{DOMAIN}", "TXT", dmarc, match_prefix="v=DMARC1")


def configure_dns(state=None, host=None):
    lan_ip = NETWORK["lan_ip"]

    # Internal-only subdomains (everything not flagged `public_dns=True`) are
    # excluded — Pi-hole still resolves them for LAN/VPN clients via
    # /etc/pihole/custom.list.
    for subdomain in PUBLIC_SUBDOMAINS:
        _upsert(f"{subdomain}.{DOMAIN}", "A", lan_ip)

    # wg AAAA record is managed by cloudflare-ddns.sh on the Pi (IPv6 via passthrough)
    if WIREGUARD.get("public_ipv4"):
        public_ip = _public_ip()
        _upsert(f"wg.{DOMAIN}", "A", public_ip)

    _reap_orphan_records(lan_ip, set(PUBLIC_SUBDOMAINS) | {"wg"})

    if EMAIL:
        _configure_email_dns()


python.call(name="Configure Cloudflare DNS records", function=configure_dns)
