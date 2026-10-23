#!/usr/bin/env bash
# =====================================================================
# uninstall.sh - remove the turing-slideshow systemd service.
# =====================================================================
# Stops and disables the service, removes the systemd unit and the udev
# rules. Does NOT delete your images folder, your config.yaml, or the
# turing-slideshow source code itself - those live inside the parent
# repo and you can remove them manually with `rm -rf` if you want.
# =====================================================================

set -euo pipefail

SERVICE_DST="/etc/systemd/system/turing-slideshow.service"
UDEV_DST="/etc/udev/rules.d/99-turing-smart-screen.rules"

if [[ $EUID -ne 0 ]]; then
    echo "Please run as root (use: sudo $0)" >&2
    exit 1
fi

echo "[INFO]  Stopping turing-slideshow.service (if running)..."
systemctl stop turing-slideshow.service 2>/dev/null || true

echo "[INFO]  Disabling turing-slideshow.service..."
systemctl disable turing-slideshow.service 2>/dev/null || true

echo "[INFO]  Removing $SERVICE_DST..."
rm -f "$SERVICE_DST"

echo "[INFO]  Removing $UDEV_DST..."
rm -f "$UDEV_DST"
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger 2>/dev/null || true

systemctl daemon-reload
echo "[OK]    turing-slideshow uninstalled."
echo "        Your config.yaml and images/ folder are untouched."
