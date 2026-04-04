"""
Bitwarden CLI helpers. Requires BW_SESSION env var to be set:
    set -x BW_SESSION (bw unlock --raw)

Item structure in the 'raspi' folder:
  cloudflare        login  password=api_token  fields: zone_id
  audiobookshelf    login  username/password   fields: cifs_username, cifs_password
  wireguard-portal  login  username/password   fields: api_token
  wireguard-server-key     (no login)          fields: private_key (hidden), public_key
  hcc               secure note (notes = env file contents)
  pihole            login  password=admin_password
  kuma-uptime       login  username/password   (used for first-run web UI setup)
"""

import functools
import json
import subprocess


def _bw(*args):
    result = subprocess.run(
        ["bw"] + list(args),
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


@functools.cache
def _folder_id():
    folders = json.loads(_bw("list", "folders"))
    match = next((f for f in folders if f["name"] == "raspi"), None)
    if not match:
        raise RuntimeError(
            "Bitwarden folder 'raspi' not found.\n"
            'Run: bw create folder (echo \'{"name":"raspi"}\' | bw encode)'
        )
    return match["id"]


@functools.cache
def _get_item(name):
    items = json.loads(_bw("list", "items", "--search", name, "--folderid", _folder_id()))
    matches = [i for i in items if i["name"] == name]
    if not matches:
        raise RuntimeError(f"Bitwarden item 'raspi/{name}' not found")
    return matches[0]


def _fields(item_name) -> dict:
    item = _get_item(item_name)
    return {f["name"]: f["value"] for f in (item.get("fields") or [])}


def hcc_env() -> str:
    return (_get_item("hcc")["notes"] or "").strip() + "\n"


def pihole_password() -> str:
    return _get_item("pihole")["login"]["password"]


def cifs_creds() -> str:
    f = _fields("audiobookshelf")
    return f"username={f['cifs_username']}\npassword={f['cifs_password']}\n"


def abs_creds() -> dict:
    login = _get_item("audiobookshelf")["login"]
    return {"username": login["username"], "password": login["password"]}


def wg_portal_creds() -> dict:
    item = _get_item("wireguard-portal")
    return {
        "username": item["login"]["username"],
        "password": item["login"]["password"],
        "api_token": _fields("wireguard-portal").get("api_token", ""),
    }


def cloudflare() -> dict:
    item = _get_item("cloudflare")
    return {
        "token": item["login"]["password"],
        "zone_id": _fields("cloudflare")["zone_id"],
    }


def kuma_creds() -> dict:
    login = _get_item("kuma-uptime")["login"]
    return {"username": login["username"], "password": login["password"]}


def wg_server_key() -> dict:
    f = _fields("wireguard-server-key")
    return {k: v for k, v in f.items() if v}


def save_wg_server_key(private_key: str, public_key: str) -> None:
    item = json.loads(json.dumps(_get_item("wireguard-server-key")))  # copy
    item["fields"] = [
        {"name": "private_key", "value": private_key, "type": 1},
        {"name": "public_key", "value": public_key, "type": 0},
    ]
    encoded = subprocess.run(
        ["bw", "encode"],
        input=json.dumps(item),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    subprocess.run(
        ["bw", "edit", "item", item["id"], encoded],
        capture_output=True,
        text=True,
        check=True,
    )
    _get_item.cache_clear()
