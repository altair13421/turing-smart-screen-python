#!/usr/bin/env bash
# =====================================================================
# install.sh - install the turing-slideshow systemd service on Linux.
# =====================================================================
#
# This installer assumes you have already cloned and set up the parent
# turing-smart-screen-python repo (Python deps installed, screen detected
# via configure.py or main.py). See:
#   https://github.com/mathoudebine/turing-smart-screen-python/wiki
#
# Usage:
#   sudo SLIDESHOW_USER=$USER ./install.sh
#
# What it does:
#   1. Verifies the parent repo path and the Python interpreter.
#   2. Ensures your user is in the `dialout` group (serial port access).
#   3. Installs the udev rules (so the smart screen is accessible).
#   4. Substitutes your username into the systemd unit and copies it to
#      /etc/systemd/system/.
#   5. Enables the service so it starts at every boot.
#   6. Optionally starts it now so you can verify it works.
#
# After install:
#   journalctl -u turing-slideshow -f       # follow logs
#   sudo systemctl stop turing-slideshow     # stop
#   sudo systemctl restart turing-slideshow # restart
#
# =====================================================================

set -euo pipefail

# ------------------------- defaults ----------------------------------
# Repo root: the parent turing-smart-screen-python checkout.
# Default is /opt/turing-smart-screen-python; override with REPO_ROOT=
REPO_ROOT="${REPO_ROOT:-/opt/turing-smart-screen-python}"
SLIDESHOW_USER="${SLIDESHOW_USER:-$USER}"
SLIDESHOW_GROUP="${SLIDESHOW_GROUP:-$SLIDESHOW_USER}"
PYTHON_BIN="${PYTHON_BIN:-}"
DO_START_NOW="${DO_START_NOW:-1}" # 1 = start the service immediately

THIS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_SRC="${THIS_DIR}/systemd/turing-slideshow.service"
UDEV_SRC="${THIS_DIR}/udev/99-turing-smart-screen.rules"

# ------------------------- helpers -----------------------------------
c_red() { printf '\033[31m%s\033[0m\n' "$*"; }
c_green() { printf '\033[32m%s\033[0m\n' "$*"; }
c_yellow() { printf '\033[33m%s\033[0m\n' "$*"; }
c_blue() { printf '\033[34m%s\033[0m\n' "$*"; }

err() {
  c_red "[ERROR] $*" >&2
  exit 1
}
info() { c_blue "[INFO]  $*"; }
ok() { c_green "[OK]    $*"; }
warn() { c_yellow "[WARN]  $*"; }

# ------------------------- preflight ---------------------------------
if [[ $EUID -ne 0 ]]; then
  err "Please run this installer as root (use: sudo $0)"
fi

if [[ ! -d "$REPO_ROOT" ]]; then
  err "Repo root not found: $REPO_ROOT
Set REPO_ROOT=<path> or move your clone to that location."
fi

if [[ ! -f "$REPO_ROOT/turing-slideshow/slideshow.py" ]]; then
  err "slideshow.py not found at $REPO_ROOT/turing-slideshow/slideshow.py
Make sure the turing-slideshow folder lives INSIDE the parent repo."
fi

if [[ ! -f "$SERVICE_SRC" ]]; then
  err "Missing systemd unit template: $SERVICE_SRC"
fi

if [[ ! -f "$UDEV_SRC" ]]; then
  err "Missing udev rules file: $UDEV_SRC"
fi

# Auto-pick a Python interpreter if not overridden.
if [[ -z "$PYTHON_BIN" ]]; then
  if [[ -x "$REPO_ROOT/venv/bin/python3" ]]; then
    PYTHON_BIN="$REPO_ROOT/venv/bin/python3"
  elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
  else
    err "No python3 found. Install Python 3.9-3.13 first."
  fi
fi
info "Using Python: $PYTHON_BIN"

# Sanity-check the parent library is importable.
if ! sudo -u "$SLIDESHOW_USER" "$PYTHON_BIN" -c \
  "import sys; sys.path.insert(0, '$REPO_ROOT'); \
     from library.lcd.lcd_comm import Orientation; print('library OK')" \
  >/dev/null 2>&1; then
  err "Cannot import the turing-smart-screen-python library from $REPO_ROOT
Make sure the parent repo is set up (pip install -r requirements.txt)."
fi
ok "Parent library is importable."

# ------------------------- dialout group -----------------------------
if ! id "$SLIDESHOW_USER" >/dev/null 2>&1; then
  err "User $SLIDESHOW_USER does not exist."
fi
if ! id -nG "$SLIDESHOW_USER" | grep -qw dialout; then
  warn "User $SLIDESHOW_USER is NOT in the 'dialout' group."
  info "Adding $SLIDESHOW_USER to dialout..."
  usermod -a -G dialout "$SLIDESHOW_USER"
  warn "The group change only takes effect after the user logs out and back in."
  warn "If the service fails to open the COM port, reboot or run 'su - $SLIDESHOW_USER'."
else
  ok "User $SLIDESHOW_USER is already in the dialout group."
fi

# ------------------------- udev rules --------------------------------
info "Installing udev rules -> /etc/udev/rules.d/99-turing-smart-screen.rules"
cp "$UDEV_SRC" /etc/udev/rules.d/99-turing-smart-screen.rules
udevadm control --reload-rules || true
udevadm trigger || true
ok "udev rules installed (includes ModemManager ignore + USB autosuspend off)."

# ------------------------- ModemManager -------------------------------
# ModemManager probes every USB-serial device at login, sending AT
# commands that scramble the screen and turn it off. The udev rule
# above sets ID_MM_DEVICE_IGNORE=1, which ModemManager respects - but
# only after a restart. If ModemManager is running, restart it so the
# new rule takes effect immediately.
if systemctl is-active --quiet ModemManager 2>/dev/null; then
  info "ModemManager is running - restarting it to apply ID_MM_DEVICE_IGNORE."
  systemctl restart ModemManager || true
  ok "ModemManager restarted - it will no longer probe the smart screen."
else
  info "ModemManager is not running - no action needed."
fi

# ------------------------- systemd unit ------------------------------
SERVICE_DST="/etc/systemd/system/turing-slideshow.service"
info "Generating $SERVICE_DST from template..."

# Substitute User / Group / WorkingDirectory / ExecStart based on env.
TMP="$(mktemp)"
sed \
  -e "s|__SLIDESHOW_USER__|$SLIDESHOW_USER|g" \
  -e "s|__SLIDESHOW_GROUP__|$SLIDESHOW_GROUP|g" \
  -e "s|WorkingDirectory=.*|WorkingDirectory=$REPO_ROOT|" \
  -e "s|ExecStart=.*|ExecStart=$PYTHON_BIN $REPO_ROOT/turing-slideshow/slideshow.py|" \
  "$SERVICE_SRC" >"$TMP"

install -m 0644 "$TMP" "$SERVICE_DST"
rm -f "$TMP"
ok "Installed $SERVICE_DST"

# ------------------------- enable ------------------------------------
info "Enabling turing-slideshow.service..."
systemctl daemon-reload
systemctl enable turing-slideshow.service

if [[ "$DO_START_NOW" == "1" ]]; then
  info "Starting turing-slideshow.service..."
  systemctl restart turing-slideshow.service
  sleep 2
  if systemctl is-active --quiet turing-slideshow.service; then
    ok "Service is running. Last 10 log lines:"
    journalctl -u turing-slideshow -n 10 --no-pager || true
  else
    warn "Service did not become active. Last 30 log lines:"
    journalctl -u turing-slideshow -n 30 --no-pager || true
  fi
else
  ok "Service is enabled; it will start on next boot."
  ok "Start it now with:  sudo systemctl start turing-slideshow"
fi

cat <<EOF

=================================================================
 turing-slideshow installed.
=================================================================

 Status / logs:
   systemctl status turing-slideshow
   journalctl -u turing-slideshow -f

 Edit config (interval, folder, fit mode, ...):
   \$EDITOR $REPO_ROOT/turing-slideshow/config.yaml
   sudo systemctl restart turing-slideshow

 To uninstall:
   sudo $REPO_ROOT/turing-slideshow/uninstall.sh

EOF
