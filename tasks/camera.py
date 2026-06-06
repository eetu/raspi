"""Pi camera enablement + system CV libraries for the camera node (raspo).

`camera` feature. Installs the libraries ocular's native venv picks up via
--system-site-packages (picamera2 + libcamera, numpy, Pillow) — ocular itself is
deployed by tasks/ocular.py. picamera2 is apt-only on the Pi (libcamera ARM
stack); fastapi/uvicorn come from pip in the venv.

Idempotent. On a stock Raspberry Pi OS the camera is auto-detected and rpicam
tooling is preinstalled; this task just guarantees the Python stack + the
config.txt flag for a fresh image.
"""

from pyinfra.operations import apt, server

apt.packages(
    name="Install Pi camera + CV libraries",
    packages=[
        "python3-picamera2",  # libcamera Python bindings (pulls libcamera + numpy)
        "python3-pil",  # Pillow — JPEG encode + overlay drawing
        "python3-numpy",  # explicit (ocular uses it directly)
        "python3-venv",  # ocular runs from a venv with --system-site-packages
    ],
    update=True,
)

server.shell(
    name="Enable camera_auto_detect in config.txt",
    commands=[
        """
        CFG=/boot/firmware/config.txt
        if ! grep -q '^camera_auto_detect=1' "$CFG"; then
          if grep -q '^camera_auto_detect=' "$CFG"; then
            sed -i 's/^camera_auto_detect=.*/camera_auto_detect=1/' "$CFG"
          else
            echo 'camera_auto_detect=1' >> "$CFG"
          fi
          echo "REBOOT_REQUIRED: camera_auto_detect enabled"
        fi
        """,
    ],
)

server.shell(
    name="Verify camera is detected",
    commands=["rpicam-hello --list-cameras 2>&1 | head -5 || true"],
)
