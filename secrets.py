"""
Bitwarden CLI helpers. Requires BW_SESSION env var to be set:
    set -x BW_SESSION (bw unlock --raw)
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


def _parse_notes(item_name):
    notes = _get_item(item_name)["notes"] or ""
    return dict(line.split("=", 1) for line in notes.splitlines() if "=" in line)


def hcc_env() -> str:
    return (_get_item("hcc")["notes"] or "").strip() + "\n"


def pihole_password() -> str:
    return _get_item("pihole")["login"]["password"]


def cifs_creds() -> str:
    login = _get_item("cifs-audiobooks")["login"]
    return f"username={login['username']}\npassword={login['password']}\n"


def wg_portal_creds() -> dict:
    login = _get_item("wireguard-portal")["login"]
    return {"username": login["username"], "password": login["password"]}


def cloudflare() -> dict:
    return _parse_notes("cloudflare")


def wg_server_key() -> dict:
    data = _parse_notes("wireguard-server-key")
    return {k: v for k, v in data.items() if v}


def save_wg_server_key(private_key: str, public_key: str) -> None:
    item = json.loads(json.dumps(_get_item("wireguard-server-key")))  # copy
    item["notes"] = f"private_key={private_key}\npublic_key={public_key}"
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
