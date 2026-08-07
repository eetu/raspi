"""Create the initial NetBird owner via POST /api/setup and capture its API token.

This bootstraps the *server*. `/api/setup` is the one unauthenticated endpoint and
it only answers while no account exists; with `NB_SETUP_PAT_ENABLED=true` on the
server (set in tasks/netbird.py) and `create_pat: true` in the body it returns a
plaintext Personal Access Token. That token is what lets the deploy register the
Kanidm OIDC connector without anyone opening the dashboard.

What it does NOT do is create the account you actually use. NetBird binds an
identity to an account by its Dex `sub` with no email fallback, so the
local-connector owner created here can never be logged into via Kanidm: the human's
first Kanidm login makes its own account, and the deploy is pointed at that one by
replacing the vault's `pat` field with a token minted there, once, by hand. The
connector is global (it lives in idp.db, not in an account), which is why
registering it from this vestigial account is enough. See the "one manual step"
section of CLAUDE.md before attempting to automate this away — it is a property of
NetBird's account model, not an oversight here.

Shape mirrors tasks/kanidm_oidc.py: a `server.shell` step talks to the service on
loopback and leaves the generated credential in a file, then a local
`python.call` step lifts it into the vault (the only place that knows how to
write secrets). Idempotent in two layers — the shell step short-circuits on
`setup_required: false`, and the vault write skips when the value is unchanged.

Token *renewal* is deliberately not here: tasks/netbird_reconcile.py already has
an authenticated API client, so it rotates the PAT before the 365-day cap as one
more piece of declarative state.
"""

from pyinfra import logger
from pyinfra.operations import python, server

import vault
from tasks.util import optional, ssh_cat, ssh_rm

NETBIRD = optional("NETBIRD")

# Where the shell step parks the token for the local step to pick up. Under the
# server's own data dir so it inherits that directory's 750 mode.
_PAT_FILE = "/var/lib/netbird-server/.pat"


if NETBIRD is not None:
    _API = f"http://{NETBIRD['host']}:{NETBIRD['port']}/api"

    # The owner credentials are exported into the environment rather than passed
    # as argv, so the password never shows up in the Pi's process table (same
    # reason tasks/kanidm_oidc.py exports __KDM_PASS).
    server.shell(
        name="Bootstrap the NetBird owner + API token if setup is required",
        commands=[
            f"""
            API="{_API}"

            # netbird-server migrates its store on first boot, so allow 2 minutes.
            for i in $(seq 1 30); do
              curl -fsS -o /dev/null "$API/instance" && break
              sleep 4
            done
            if ! curl -fsS -o /dev/null "$API/instance"; then
              echo "netbird: server not answering on $API after 2 min — skipping bootstrap" >&2
              exit 0
            fi

            SETUP_REQUIRED=$(curl -fsS "$API/instance" \\
              | python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("setup_required", False)).lower())')
            if [ "$SETUP_REQUIRED" != "true" ]; then
              echo "netbird: already bootstrapped — skipping /api/setup"
              exit 0
            fi

            . /etc/secrets/netbird.env
            if [ -z "${{NB_BOOTSTRAP_OWNER_PASSWORD:-}}" ]; then
              echo "netbird: bootstrap password missing — run tasks/secrets.py first" >&2
              exit 0
            fi
            export NB_BOOTSTRAP_OWNER_EMAIL NB_BOOTSTRAP_OWNER_PASSWORD

            # The response body carries the plaintext token, so it is never echoed
            # — it goes straight to a 600 file for the local step to collect.
            # pat_expire_in is capped at 365 days by the API; reconcile rotates it.
            umask 077
            python3 << 'PYEOF'
import json, os, sys, urllib.error, urllib.request

body = json.dumps({{
    "email": os.environ["NB_BOOTSTRAP_OWNER_EMAIL"],
    "password": os.environ["NB_BOOTSTRAP_OWNER_PASSWORD"],
    "name": "{NETBIRD["bootstrap_owner_name"]}",
    "create_pat": True,
    "pat_expire_in": 365,
}}).encode()
req = urllib.request.Request(
    "{_API}/setup",
    data=body,
    method="POST",
    headers={{"Content-Type": "application/json"}},
)
try:
    with urllib.request.urlopen(req) as r:
        payload = json.load(r)
except urllib.error.HTTPError as e:
    print(f"netbird: /api/setup failed: {{e.code}} {{e.reason}}", file=sys.stderr)
    sys.exit(1)

token = payload.get("personal_access_token", "")
if not token:
    print(
        "netbird: owner created but no token returned — check that "
        "NB_SETUP_PAT_ENABLED=true reached the server container.",
        file=sys.stderr,
    )
    sys.exit(1)

with open("{_PAT_FILE}", "w") as f:
    f.write(token)
print("netbird: owner created, API token captured")
PYEOF
            unset NB_BOOTSTRAP_OWNER_EMAIL NB_BOOTSTRAP_OWNER_PASSWORD
            """,
        ],
    )

    def _save_pat(state=None, host=None):
        """Move the setup token from the Pi into the vault, then delete it.

        Runs locally because the vault only exists on the control host. A missing
        file is the normal case on every deploy after the first.
        """
        token = ssh_cat(_PAT_FILE)
        if not token:
            return
        if vault.netbird_pat() != token:
            vault.save_netbird_pat(token)
            logger.info("netbird: API token saved to the vault")
        ssh_rm(_PAT_FILE)

    python.call(
        name="Save the NetBird API token to the secret store",
        function=_save_pat,
    )
