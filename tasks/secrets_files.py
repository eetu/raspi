"""Write secrets from Bitwarden to /etc/secrets/ on the Pi."""

import io

from pyinfra.operations import files

import vault as bw
from group_data.all import CIFS


def _put_secret(name, content, dest, mode="600", group="root"):
    files.put(
        name=f"Write secret: {name}",
        src=io.BytesIO(content.encode()),
        dest=dest,
        user="root",
        group=group,
        mode=mode,
    )


_put_secret("hcc.env", bw.hcc_env(), "/etc/secrets/hcc.env")

for _name in CIFS:
    _put_secret(f"cifs-{_name}", bw.cifs_creds(_name), f"/etc/secrets/cifs-{_name}")

cf = bw.cloudflare()
_put_secret(
    "cloudflare.env",
    f"CF_DNS_API_TOKEN={cf['token']}\nzone_id={cf['zone_id']}\n",
    "/etc/secrets/cloudflare.env",
    mode="640",
    group="traefik",
)
