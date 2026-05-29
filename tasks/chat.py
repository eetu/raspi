"""Chat: Podman Quadlet container unit.

Optional service — comment the CHAT dict in group_data/all.py to retire
it; the task then stops + disables the `chat` unit and leaves
/var/lib/chat untouched for rollback.

Two-deploy bootstrap for Kanidm OIDC — deploy 1 registers the `chat` client in
Kanidm and writes the generated secret to BW; deploy 2 reads the secret out of
BW (via tasks/secrets.py) and wires it into the container env. Until then chat
runs without OIDC (login route returns 503 unless DEV_AUTH=1, which we never
set in production).

The AI / COMFY / STT / TTS backends are off-Pi (Mac mini) and independently
optional — each contributes one upstream URL env var only when its dict is
present, so chat deploys fine with any subset of them configured.
"""

import hashlib
import io
import json

from pyinfra.operations import files, server, systemd

from tasks.util import optional

CHAT = optional("CHAT")


if CHAT is None:
    # Retired: keep state on disk, stop + disable the unit.
    systemd.service(
        name="Stop + disable chat (kept on disk for rollback)",
        service="chat",
        running=False,
        enabled=False,
        daemon_reload=True,
    )
else:
    AI = optional("AI")
    COMFY = optional("COMFY")
    STT = optional("STT")
    TTS = optional("TTS")

    _base_env = {
        "PORT": str(CHAT["port"]),
        "CHAT_DB_PATH": "/data/chat.db",
    }
    # Off-Pi backend URLs — only set when the backend's dict is configured.
    if AI:
        _base_env["OLLAMA_URL"] = f"http://{AI['host']}:{AI['port']}"
    if COMFY:
        _base_env["COMFYUI_URL"] = f"http://{COMFY['host']}:{COMFY['port']}"
    if STT:
        _base_env["WHISPER_URL"] = f"http://{STT['host']}:{STT['port']}"
    if TTS:
        _base_env["PIPER_URL"] = f"http://{TTS['host']}:{TTS['port']}"

    def _env_line(k: str, v) -> str:
        if not isinstance(v, str):
            v = json.dumps(v, ensure_ascii=False)
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'Environment="{k}={escaped}"'

    _env_lines = "\n".join(_env_line(k, v) for k, v in {**_base_env, **CHAT["env"]}.items())

    quadlet = f"""\
[Unit]
Description=Chat — self-hosted Ollama UI
After=network-online.target
Wants=network-online.target

[Container]
ContainerName=chat
Image={CHAT["image"]}
Network=host
{_env_lines}
EnvironmentFile=/etc/secrets/chat.env
Volume=/var/lib/chat:/data
AutoUpdate=registry
Pull=newer

[Service]
Restart=always
RestartSec=10
TimeoutStartSec=300
MemoryMax=128M
MemorySwapMax=64M

[Install]
WantedBy=multi-user.target
"""

    _quadlet_hash = hashlib.sha256(quadlet.encode()).hexdigest()

    files.directory(
        name="Create /var/lib/chat",
        path="/var/lib/chat",
        user="root",
        group="root",
        mode="777",
        present=True,
    )

    files.put(
        name="Write chat.container quadlet",
        src=io.BytesIO(quadlet.encode()),
        dest="/etc/containers/systemd/chat.container",
        user="root",
        group="root",
        mode="644",
    )

    server.shell(
        name="Reload quadlet units",
        commands=[
            "/usr/lib/systemd/system-generators/podman-system-generator /run/systemd/generator 2>/dev/null || true"
        ],
    )

    systemd.service(
        name="Start Chat",
        service="chat",
        running=True,
        daemon_reload=True,
    )

    server.shell(
        name="Restart Chat if quadlet changed",
        commands=[
            f"""
            STAMP=/etc/containers/systemd/.chat-quadlet-stamp
            if [ "$(cat "$STAMP" 2>/dev/null)" != "{_quadlet_hash}" ]; then
              systemctl restart chat
              echo '{_quadlet_hash}' > "$STAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Restart Chat if env changed",
        commands=[
            """
            ESTAMP=/etc/secrets/.chat-env-stamp
            ENV_HASH=$(sha256sum /etc/secrets/chat.env | cut -d' ' -f1)
            if [ "$(cat "$ESTAMP" 2>/dev/null)" != "$ENV_HASH" ]; then
              systemctl restart chat
              echo "$ENV_HASH" > "$ESTAMP"
            fi
            """,
        ],
    )

    server.shell(
        name="Pull latest Chat image and restart if updated",
        commands=[
            f"""
            NEW=$(podman pull -q {CHAT["image"]})
            CUR=$(podman inspect --format '{{{{.Image}}}}' chat 2>/dev/null || echo "")
            if [ "$NEW" != "$CUR" ]; then
              systemctl restart chat
            fi
            """,
        ],
    )
