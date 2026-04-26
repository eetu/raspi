"""User shell setup: install fish/zsh + tools, set the default shell, wire up integrations.

Run this task on its own to switch shells without re-running the full deploy:
    uv run pyinfra inventory.py tasks/shell.py
"""

from pyinfra.context import host
from pyinfra.operations import apt, files, server

from group_data.all import SHELL

apt.packages(
    name="Install shells and integrations",
    packages=["fish", "zsh", "fzf", "zoxide"],
    update=True,
)

current_user = host.data.ssh_user

if current_user:
    server.user(
        name=f"Set {SHELL} for {current_user}",
        user=current_user,
        shell=SHELL,
        _sudo=True,
    )

if "fish" in SHELL:
    files.directory(
        name="Ensure fish config dir exists",
        path=f"/home/{current_user}/.config/fish",
        user=current_user,
        present=True,
    )

    files.line(
        name="Initialize zoxide in fish",
        path=f"/home/{current_user}/.config/fish/config.fish",
        line="zoxide init fish | source",
        present=True,
    )

    files.line(
        name="Initialize fzf keybindings in fish",
        path=f"/home/{current_user}/.config/fish/config.fish",
        line="fzf_key_bindings",
        present=True,
    )

elif "zsh" in SHELL:
    files.line(
        name="Initialize zoxide in zsh",
        path=f"/home/{current_user}/.zshrc",
        line='eval "$(zoxide init zsh)"',
        present=True,
    )
