"""Audiobookshelf: install via official PPA, configure port."""

from pyinfra.operations import apt, files, server, systemd

from group_data.all import AUDIOBOOKSHELF

# --- PPA ---

server.shell(
    name="Add Audiobookshelf GPG key",
    commands=[
        "wget -qO- https://advplyr.github.io/audiobookshelf-ppa/KEY.gpg "
        "| gpg --dearmor "
        "| tee /etc/apt/trusted.gpg.d/audiobookshelf.gpg > /dev/null",
    ],
)

files.put(
    name="Add Audiobookshelf apt source",
    src="files/audiobookshelf.list",
    dest="/etc/apt/sources.list.d/audiobookshelf.list",
    user="root",
    group="root",
    mode="644",
)

apt.update(name="Update apt cache after adding audiobookshelf PPA")

apt.packages(
    name="Install Audiobookshelf",
    packages=["audiobookshelf"],
    update=False,
)

# --- Default env: bind to localhost, set port ---

server.shell(
    name="Configure Audiobookshelf port and host",
    commands=[
        "grep -q '^PORT=' /etc/default/audiobookshelf "
        f"  && sed -i 's/^PORT=.*/PORT={AUDIOBOOKSHELF['port']}/' /etc/default/audiobookshelf "
        f"  || echo 'PORT={AUDIOBOOKSHELF['port']}' >> /etc/default/audiobookshelf",
        "grep -q '^HOST=' /etc/default/audiobookshelf "
        f"  && sed -i 's/^HOST=.*/HOST={AUDIOBOOKSHELF['host']}/' /etc/default/audiobookshelf "
        f"  || echo 'HOST={AUDIOBOOKSHELF['host']}' >> /etc/default/audiobookshelf",
    ],
)

systemd.service(
    name="Enable Audiobookshelf",
    service="audiobookshelf",
    enabled=True,
    running=True,
    daemon_reload=True,
)
