# turing-slideshow

A custom image slideshow for the **`turing-smart-screen-python`** project.
Display every image in a folder on your USB-C smart screen (Turing / XuanFang
/ UsbPCMonitor / WeAct / Kipye), rotating on a timer, and start automatically
on every boot.

This is a **companion package** — it lives *inside* an existing clone of
[`mathoudebine/turing-smart-screen-python`](https://github.com/mathoudebine/turing-smart-screen-python)
and reuses that project's display library.

```
┌────────────────────────────────────────────────────────────────┐
│  turing-smart-screen-python/        <- the parent repo         │
│  ├── library/                                                    │
│  ├── res/                                                       │
│  ├── requirements.txt                                           │
│  └── turing-slideshow/               <- THIS repo (you push)    │
│      ├── slideshow.py                                           │
│      ├── config.yaml                                            │
│      ├── install.sh                                             │
│      ├── uninstall.sh                                           │
│      ├── systemd/                                               │
│      ├── udev/                                                  │
│      ├── images/                                                │
│      └── README.md                                              │
└────────────────────────────────────────────────────────────────┘
```

---

## What it does

* **Slideshow only.** No CPU/GPU sensors, no stats — just images. Drop files
  in the folder and they appear on the screen.
* **Auto-detects the screen.** Probes each supported `LcdComm` subclass
  (`LcdCommRevA` → `LcdCommRevB` → `LcdCommRevD` → `LcdCommRevC` →
  `LcdCommWeActA` → `LcdCommWeActB` → `LcdCommTuringUSB`). If nothing is
  found, falls back to `LcdSimulated` (a window on the host).
* **Re-scans the folder on every cycle.** You can drop a new image into the
  folder at any time; it will appear on the next pass — no restart needed.
* **Recursive.** Walks subfolders too (toggleable in `config.yaml`).
* **Contain-fit by default.** Scales each image to fit entirely inside the
  screen, centered, with black bars filling the remainder (no cropping).
  Other modes (`cover`, `stretch`, `native`) are available.
* **30 seconds per slide, alphabetical order** (configurable: shuffle,
  interval).
* **Graceful shutdown.** On `SIGINT` / `SIGTERM` the screen is cleared and
  the serial port closed cleanly. systemd's `Restart=on-failure` brings it
  back automatically on USB glitches.

---

## Requirements

* Linux (Ubuntu / Debian / Raspberry Pi OS / Arch … anything with systemd).
  *Windows and macOS support is possible but the auto-start scripts here are
  Linux-only.*
* Python **3.9 — 3.13** (the parent project does NOT support 3.14 yet).
* A working clone of `turing-smart-screen-python` with `requirements.txt`
  installed. See the parent project's wiki:
  <https://github.com/mathoudebine/turing-smart-screen-python/wiki/System-monitor-:-how-to-start>
* A supported smart screen (any revision works).
* Your user must be in the `dialout` group so the serial port is accessible.

---

## Quick start

```bash
# 1. Clone the parent repo (skip if already done)
sudo mkdir -p /opt && sudo chown "$USER":"$USER" /opt
git clone https://github.com/mathoudebine/turing-smart-screen-python.git /opt/turing-smart-screen-python
cd /opt/turing-smart-screen-python

# 2. (optional) set up a venv
python3 -m venv venv
. venv/bin/activate

# 3. Install parent project dependencies
pip install -r requirements.txt

# 4. Place this slideshow package inside the parent repo
git clone https://github.com/YOUR-USERNAME/turing-slideshow.git turing-slideshow
#  (or:  cp -r /path/to/this/folder  ./turing-slideshow)

# 5. Put some images in the default folder
cp ~/Pictures/*.jpg turing-slideshow/images/

# 6. Test it now (foreground, watch logs)
python3 turing-slideshow/slideshow.py --once

# 7. If that works, run the slideshow in foreground (Ctrl-C to stop)
python3 turing-slideshow/slideshow.py

# 8. Install the systemd service so it auto-starts on every boot
sudo SLIDESHOW_USER="$USER" ./turing-slideshow/install.sh
```

After step 8, the slideshow runs on every boot under systemd.

---

## Configuration

Everything you need to tweak lives in **`config.yaml`** next to `slideshow.py`.
Edit it once, then restart the service:

```bash
$EDITOR turing-slideshow/config.yaml
sudo systemctl restart turing-slideshow
```

| Key | Default | Meaning |
| --- | --- | --- |
| `images_folder` | `./turing-slideshow/images` | Path to your image folder. Absolute, `~`-expanded, or relative to the parent repo root. |
| `interval_seconds` | `30` | How long each slide stays on screen. |
| `shuffle` | `false` | Randomize slide order on each pass. |
| `recursive` | `true` | Walk subfolders too. |
| `fit_mode` | `contain` | `contain` (fit + black bars), `cover` (fill + crop), `stretch` (distort), `native` (center, fall back to contain if too big). |
| `background_color` | `[0, 0, 0]` | RGB color of the empty area in `contain` / `native` modes. |
| `display.revision` | `AUTO` | Force a specific revision: `A` `B` `C` `D` `TUR_USB` `WEACT_A` `WEACT_B` `SIMU`. |
| `display.com_port` | `AUTO` | Override the COM port (e.g. `/dev/ttyACM0`, `COM3`). Ignored for `TUR_USB` / `SIMU`. |
| `display.orientation` | `LANDSCAPE` | `PORTRAIT` `LANDSCAPE` `REVERSE_PORTRAIT` `REVERSE_LANDSCAPE`. |
| `display.brightness` | `100` | Backlight brightness 0..100. Some revisions ignore this. |
| `display.width` | `320` | Screen pixel width in PORTRAIT orientation (3.5" default). Override for 5" (480x800), 8.8" (480x1920), etc. |
| `display.height` | `480` | Screen pixel height in PORTRAIT orientation. |
| `log_level` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR`. |

See [`examples/example_config.yaml`](examples/example_config.yaml) for several
ready-to-use variants (USB-stick slideshow, simulator mode, museum kiosk,
native-resolution-only).

### Supported image formats

`.jpg .jpeg .png .bmp .gif .webp` (animated GIFs are flattened to their
first frame).

### Folder watching

The folder is **re-scanned on every cycle**, not via `inotify`. This is
intentional — it works on every filesystem, costs almost nothing (one
`os.walk` every N seconds), and avoids missing files that are mid-copy.
If you copy a 50 MB image over a slow link, it will be picked up only
once the write is complete (Pillow's `Image.open().load()` would raise
on a truncated file and we just skip it and try again next cycle).

---

## Auto-start on boot

The `install.sh` script:

1. Verifies the parent repo is set up.
2. Adds your user to the `dialout` group (required for serial-port access).
3. Installs a udev rule so the screen is readable by every user.
4. Generates `/etc/systemd/system/turing-slideshow.service` with your
   username, repo path, and Python interpreter substituted in.
5. Enables the service so it starts on every boot.

### Install

```bash
sudo SLIDESHOW_USER="$USER" ./install.sh
```

Environment overrides you can pass:

| Variable | Default | Meaning |
| --- | --- | --- |
| `REPO_ROOT` | `/opt/turing-smart-screen-python` | Path to the parent repo. |
| `SLIDESHOW_USER` | `$USER` | The user that should run the slideshow. |
| `SLIDESHOW_GROUP` | `$SLIDESHOW_USER` | The group to run as. |
| `PYTHON_BIN` | `$REPO_ROOT/venv/bin/python3` | Override the Python interpreter. |
| `DO_START_NOW` | `1` | Set to `0` to enable-and-exit without starting. |

### Operating the service

```bash
systemctl status turing-slideshow              # status
journalctl -u turing-slideshow -f              # follow logs
systemctl stop turing-slideshow                # stop
systemctl restart turing-slideshow             # restart after config edit
systemctl disable turing-slideshow             # stop auto-starting on boot
sudo ./uninstall.sh                            # full removal (keeps your images)
```

### Reinstalling / updating

```bash
cd /opt/turing-smart-screen-python/turing-slideshow
git pull
sudo ./install.sh   # idempotent: regenerates the unit and restarts
```

---

## Manual run (without systemd)

If you want to run the slideshow from a terminal (e.g. for debugging
before installing the service), just invoke the script directly:

```bash
cd /opt/turing-smart-screen-python
python3 turing-slideshow/slideshow.py            # run forever
python3 turing-slideshow/slideshow.py --once     # show first image only
python3 turing-slideshow/slideshow.py --config /path/to/other-config.yaml
python3 turing-slideshow/slideshow.py -v         # DEBUG logging
```

---

## Troubleshooting

### `cannot find the turing-smart-screen-python library`

The script couldn't find the parent project. Either:

* Make sure `turing-slideshow/` lives *inside* the parent repo:
  `/opt/turing-smart-screen-python/turing-slideshow/slideshow.py`.
* **Or** set `TURING_SMART_SCREEN_ROOT=/path/to/parent/repo` before running.

### Screen is blank / service shows "no supported screen detected"

* Check your screen is plugged in and detected by the kernel:
  ```bash
  ls /dev/ttyACM* /dev/ttyUSB*
  dmesg | grep -iE 'tty|ch340|cdc_acm' | tail
  ```
* Make sure your user is in `dialout`:
  ```bash
  groups                          # should include dialout
  sudo usermod -a -G dialout $USER
  # log out & back in (or reboot)
  ```
* Force a specific revision in `config.yaml` (`display.revision: A`,
  `B`, `C`, `D`, `WEACT_A`, `WEACT_B`, `TUR_USB`) instead of `AUTO`.
* If you have *no* hardware at all, set `display.revision: SIMU` — a
  window will open on the host so you can preview the slideshow.

### Images don't appear / "No images found"

* The folder path in `config.yaml` is interpreted relative to the
  **parent repo root**, not relative to `turing-slideshow/`. For an
  absolute path use `/home/you/Pictures/slideshow`.
* Supported extensions: `.jpg .jpeg .png .bmp .gif .webp`. Mixed-case
  is fine (`.JPG` works).
* If `recursive: false`, only top-level files are scanned.

### Image looks distorted / wrong aspect ratio

* `fit_mode: contain` (default) preserves aspect ratio and pads with
  black bars — usually what you want.
* `fit_mode: cover` fills the screen entirely and crops the overflow.
* `fit_mode: stretch` matches the screen exactly but distorts the image.
* `fit_mode: native` doesn't scale — only images exactly the screen
  resolution look right; larger ones fall back to `contain`.

### Screen is upside-down / rotated

* Try a different value for `display.orientation`:
  `PORTRAIT`, `LANDSCAPE`, `REVERSE_PORTRAIT`, `REVERSE_LANDSCAPE`.

### Service crashes repeatedly

```bash
journalctl -u turing-slideshow -n 200 --no-pager
```
Common causes:

* A specific image file is corrupt → it will be skipped automatically.
  Look for `Skipping /path/to/img.jpg: ...` in the log.
* Python 3.14 (the parent project caps at 3.13). Downgrade or patch
  `library/pythoncheck.py` as described in the parent project's wiki.
* Permission error opening the COM port → see the dialout group steps
  above.

---

## File map

```
turing-slideshow/
├── README.md                              <- you are here
├── LICENSE                                <- GPL-3.0-or-later
├── requirements.txt                       <- Pillow, pyserial, PyYAML
├── config.yaml                            <- your settings (edit this)
├── slideshow.py                           <- main slideshow script
├── install.sh                             <- installs systemd service
├── uninstall.sh                           <- removes systemd service
├── systemd/
│   └── turing-slideshow.service           <- systemd unit template
├── udev/
│   └── 99-turing-smart-screen.rules       <- udev rules for serial port
├── examples/
│   └── example_config.yaml                <- alternative config samples
└── images/
    └── .gitkeep                           <- placeholder; drop images here
```

---

## Pushing to your own git

This folder is intended to be its own git repo, then dropped inside (or
cloned as a sub-folder of) the parent `turing-smart-screen-python`
checkout. Recommended setup:

```bash
cd /opt/turing-smart-screen-python

# Initialize this folder as its own repo (separate from the parent's .git)
cd turing-slideshow
git init
git add -A
git commit -m "Initial turing-slideshow release"

# Add your remote and push
git remote add origin https://github.com/YOUR-USERNAME/turing-slideshow.git
git branch -M main
git push -u origin main
```

If you'd rather embed this as a git submodule inside your parent repo:

```bash
cd /opt/turing-smart-screen-python
git submodule add https://github.com/YOUR-USERNAME/turing-slideshow.git turing-slideshow
git commit -m "Add turing-slideshow as submodule"
```

> The parent project uses GPL-3.0-or-later; this companion package is
> licensed the same way to keep licensing simple. See
> [`LICENSE`](LICENSE).

---

## License

GPL-3.0-or-later — same as the parent
[`turing-smart-screen-python`](https://github.com/mathoudebine/turing-smart-screen-python)
project. See [`LICENSE`](LICENSE) for the full text.
