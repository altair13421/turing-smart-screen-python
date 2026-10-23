#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# turing-slideshow - a custom image slideshow for turing-smart-screen-python
# https://github.com/mathoudebine/turing-smart-screen-python
#
# Displays every image in a configurable folder as a slideshow on a supported
# USB-C smart screen (Turing / XuanFang / UsbPCMonitor / WeAct / Kipye).
#
# Configuration is read from ./config.yaml next to this file. The folder is
# re-scanned on every loop iteration, so dropping a new file in is enough to
# have it picked up on the next cycle (no restart needed).
#
# Copyright (C) 2026 - released under GPL-3.0-or-later
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
turing-slideshow
================

A standalone slideshow player for USB-C smart screens supported by the
`turing-smart-screen-python` library (Turing / XuanFang / UsbPCMonitor /
WeAct / Kipye).

Features
--------
* Auto-detects the connected screen (revision A/B/C/D/TUR_USB/WEACT_A/WEACT_B)
  and falls back to the in-window simulator if no hardware is found.
* Reads all settings from `config.yaml` next to this script.
* Re-scans the images folder on every loop, so newly added files appear on
  the next cycle without a restart.
* Walks subfolders recursively (toggleable).
* 30 seconds per slide by default, alphabetical order (toggleable shuffle).
* "Contain" fit by default: scales the image to fit entirely inside the
  screen, centered, with black bars filling the remainder (no cropping).
* Graceful shutdown on SIGINT / SIGTERM: clears the screen and closes the
  serial port cleanly.

Exit codes
----------
* 0  -> clean shutdown (signal received)
* 1  -> configuration error
* 2  -> no images found
* 3  -> could not detect / open any screen
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path bootstrap.
#
# The turing-smart-screen-python library uses relative imports like
# `from library.lcd.lcd_comm import Orientation`. For those imports to work,
# this script must run with the PARENT repo root on sys.path[0], and the
# process working directory must be that same repo root (because the library
# also references relative paths like ./res/fonts/...).
#
# This slideshow is meant to live at  <repo_root>/turing-slideshow/slideshow.py
# So:
#   SCRIPT_DIR  = <repo_root>/turing-slideshow/
#   REPO_ROOT   = <repo_root>/                  (parent of SCRIPT_DIR)
#
# We add REPO_ROOT to sys.path, and chdir into it before importing.
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

if not (REPO_ROOT / "library" / "lcd" / "lcd_comm.py").exists():
    # Allow override via env var, so users who placed this folder elsewhere
    # can still point us at the right repo.
    env_root = os.environ.get("TURING_SMART_SCREEN_ROOT")
    if env_root and Path(env_root).is_dir():
        REPO_ROOT = Path(env_root).resolve()
    else:
        sys.stderr.write(
            "ERROR: cannot find the turing-smart-screen-python library.\n"
            "This script expects to live inside the repo at\n"
            "  <repo-root>/turing-slideshow/slideshow.py\n"
            "Alternatively, set TURING_SMART_SCREEN_ROOT to point at the\n"
            "cloned turing-smart-screen-python repo.\n"
        )
        sys.exit(3)

sys.path.insert(0, str(REPO_ROOT))
os.chdir(REPO_ROOT)

# Now we can import the parent project's library.
try:
    import yaml  # type: ignore
    from PIL import Image  # type: ignore
except ImportError as exc:
    sys.stderr.write(
        f"ERROR: missing Python dependency: {exc.name}\n"
        "Install requirements with:\n"
        "  pip install -r turing-slideshow/requirements.txt\n"
    )
    sys.exit(3)

# OpenCV (cv2) is optional - only needed for video playback.
# If absent, video files are silently skipped (images still work).
try:
    import cv2  # type: ignore
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

# ---------------------------------------------------------------------------
# Logging - setup early so the optional-import block below can use it.
# ---------------------------------------------------------------------------
logger = logging.getLogger("turing-slideshow")

# Library imports (must come AFTER sys.path tweak above)
from library.lcd.lcd_comm import LcdComm, Orientation  # noqa: E402
from library.lcd.lcd_comm_rev_a import LcdCommRevA  # noqa: E402
from library.lcd.lcd_comm_rev_b import LcdCommRevB  # noqa: E402
from library.lcd.lcd_comm_rev_c import LcdCommRevC  # noqa: E402
from library.lcd.lcd_comm_rev_d import LcdCommRevD  # noqa: E402
from library.lcd.lcd_comm_weact_a import LcdCommWeActA  # noqa: E402
from library.lcd.lcd_comm_weact_b import LcdCommWeActB  # noqa: E402
from library.lcd.lcd_simulated import LcdSimulated  # noqa: E402

# LcdCommTuringUSB depends on `pyusb` AND `pycryptodome` (Crypto.Cipher.DES).
# Both are optional - users with a serial-based screen (rev A/B/C/D/WEACT_*)
# never need them. Import lazily so the script doesn't crash on systems
# without those packages.
LcdCommTuringUSB = None  # will be replaced below if pyusb is available
try:
    from library.lcd.lcd_comm_turing_usb import (  # noqa: E402
        LcdCommTuringUSB as _LcdCommTuringUSB,
    )
    LcdCommTuringUSB = _LcdCommTuringUSB
except ImportError as _exc:
    # Use a plain print here in case logging isn't yet configured; logger
    # itself exists (defined above) but may have no handlers attached.
    sys.stderr.write(
        f"[INFO] pyusb/pycryptodome not available ({_exc}); TUR_USB "
        f"revision will be unavailable. This is fine if you have a "
        f"serial-based screen (rev A/B/C/D/WEACT_*).\n"
    )

# Default config values, overridable from config.yaml.
DEFAULTS = {
    "images_folder": "./images",          # may be absolute or repo-relative
    "interval_seconds": 30,                # 30s per slide (ordered)
    "shuffle": False,                      # keep alphabetical order
    "recursive": True,                     # include subfolders
    "fit_mode": "contain",                 # contain | cover | stretch | native
    "background_color": [0, 0, 0],         # black bars by default
    "rotate_image": 0,                     # 0 | 90 | 180 | 270  (software
                                           # rotation, clockwise)
    "auto_orient": False,                  # if true, pick rotation per-image:
                                           # landscape -> rotate_image,
                                           # portrait  -> portrait_rotation
    "portrait_rotation": 0,                 # rotation for portrait images
                                           # when auto_orient is true
    "videos_enabled": True,                # play .mp4/.avi/.mov/.mkv/.webm
    "video_max_fps": 24,                    # cap video playback at this FPS
    "video_max_seconds": 30,               # play each video for at most this long
    "display": {
        "revision": "AUTO",                # AUTO | A | B | C | D | TUR_USB |
        #                          WEACT_A | WEACT_B | SIMU
        "com_port": "AUTO",                 # COM port name, or AUTO
        "orientation": "PORTRAIT",        # PORTRAIT | LANDSCAPE |
        #                          REVERSE_PORTRAIT | REVERSE_LANDSCAPE
        "brightness": 100,                 # 0..100
        "width": 320,                       # screen pixel width in PORTRAIT
        "height": 480,                      # screen pixel height in PORTRAIT
    },
    "log_level": "INFO",                   # DEBUG | INFO | WARNING | ERROR
}

# Image file extensions we accept (case-insensitive).
SUPPORTED_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}

# Video file extensions we accept (case-insensitive).
# Requires the optional OpenCV (cv2) package; videos are skipped if absent.
VIDEO_EXTS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".wmv"}

# Mapping of orientation name -> Orientation enum.
ORIENTATION_MAP = {
    "PORTRAIT": Orientation.PORTRAIT,
    "LANDSCAPE": Orientation.LANDSCAPE,
    "REVERSE_PORTRAIT": Orientation.REVERSE_PORTRAIT,
    "REVERSE_LANDSCAPE": Orientation.REVERSE_LANDSCAPE,
}


# ---------------------------------------------------------------------------
# Config loading.
# ---------------------------------------------------------------------------
def load_config(path: Path) -> dict:
    """Load config.yaml and merge with defaults. Validates values too."""
    if not path.is_file():
        logger.warning("config.yaml not found at %s; using defaults only", path)
        cfg = {}
    else:
        try:
            with path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            logger.error("Could not parse %s: %s", path, exc)
            cfg = {}

    # Deep-merge: defaults are the base, cfg overrides.
    merged = {**DEFAULTS, **cfg}
    merged["display"] = {**DEFAULTS["display"], **(cfg.get("display") or {})}
    return merged


def resolve_images_folder(folder_setting: str) -> Path:
    """Resolve a folder path that may be absolute or repo-relative."""
    p = Path(folder_setting).expanduser()
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def collect_images(folder: Path, recursive: bool,
                   include_videos: bool = False) -> List[Path]:
    """Return a sorted list of image (and optionally video) paths in folder."""
    if not folder.is_dir():
        logger.error("Images folder does not exist: %s", folder)
        return []
    accepted = set(SUPPORTED_EXTS)
    if include_videos:
        accepted |= VIDEO_EXTS
    if recursive:
        walker = folder.rglob("*")
    else:
        walker = folder.glob("*")
    found = [
        p for p in walker
        if p.is_file() and p.suffix.lower() in accepted
    ]
    found.sort(key=lambda p: p.as_posix().lower())
    return found


def is_video(path: Path) -> bool:
    """True if path's extension is a recognized video format."""
    return path.suffix.lower() in VIDEO_EXTS


# ---------------------------------------------------------------------------
# Software rotation.
# ---------------------------------------------------------------------------
def rotate_image_software(img: Image.Image,
                          degrees_cw: int) -> Image.Image:
    """
    Rotate `img` clockwise by `degrees_cw` (must be 0, 90, 180, or 270).
    Returns a new image. PIL's rotate() uses counter-clockwise positive
    angles, so we negate.
    """
    degrees_cw = degrees_cw % 360
    if degrees_cw == 0:
        return img
    # PIL: positive = CCW, so use -degrees_cw for CW
    return img.rotate(-degrees_cw, expand=True)


def is_landscape(img: Image.Image) -> bool:
    """True if the image is wider than it is tall (or square)."""
    w, h = img.size
    return w >= h


def effective_rotation_for(img: Image.Image,
                            base_rotation: int,
                            auto_orient: bool,
                            portrait_rotation: int) -> int:
    """
    Decide which rotation (in degrees CW) to apply to `img`.

    * If `auto_orient` is False: always use `base_rotation`.
    * If `auto_orient` is True:
        - landscape image (w >= h): use `base_rotation`
        - portrait  image (w <  h): use `portrait_rotation`
    """
    if not auto_orient:
        return base_rotation
    if is_landscape(img):
        return base_rotation
    return portrait_rotation


def fit_dims_for_rotation(rotation_deg: int,
                          panel_w: int,
                          panel_h: int) -> Tuple[int, int]:
    """
    Return the (width, height) the source image should be fit to BEFORE
    being rotated by `rotation_deg` and sent to a `panel_w x panel_h`
    panel.

    * 0/180 degrees: the pre-rotation canvas equals the panel dims.
    * 90/270 degrees: the pre-rotation canvas is the panel dims swapped
      (because rotation swaps width and height).
    """
    if rotation_deg in (90, 270):
        return panel_h, panel_w
    return panel_w, panel_h


# ---------------------------------------------------------------------------
# Image scaling.
# ---------------------------------------------------------------------------
def fit_image(img: Image.Image,
              target_w: int,
              target_h: int,
              mode: str,
              bg_color: Tuple[int, int, int]) -> Image.Image:
    """
    Return an image of size (target_w, target_h) containing `img` fitted
    according to `mode`. The output is always RGB.

    Modes:
      contain  - scale image to fit entirely, center, fill remainder with bg
      cover    - scale image to fill screen entirely, crop overflow, center
      stretch  - resize image to exactly (target_w, target_h) (may distort)
      native   - center image as-is, fill remainder with bg (no scaling)
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    src_w, src_h = img.size
    if src_w == 0 or src_h == 0:
        raise ValueError(f"Image has zero dimension: {img.size}")

    canvas = Image.new("RGB", (target_w, target_h), bg_color)

    if mode == "stretch":
        scaled = img.resize((target_w, target_h), Image.LANCZOS)
        canvas.paste(scaled, (0, 0))
        return canvas

    if mode == "native":
        # Center as-is; if larger than screen, fall back to contain.
        if src_w <= target_w and src_h <= target_h:
            off = ((target_w - src_w) // 2, (target_h - src_h) // 2)
            canvas.paste(img, off)
            return canvas
        # else: behave like contain
        mode = "contain"

    # contain / cover share the scaling math; only the min/max differs.
    if mode == "contain":
        scale = min(target_w / src_w, target_h / src_h)
    elif mode == "cover":
        scale = max(target_w / src_w, target_h / src_h)
    else:
        logger.warning("Unknown fit_mode '%s'; falling back to contain", mode)
        scale = min(target_w / src_w, target_h / src_h)

    new_w = max(1, int(round(src_w * scale)))
    new_h = max(1, int(round(src_h * scale)))
    scaled = img.resize((new_w, new_h), Image.LANCZOS)

    if mode == "cover":
        # Crop centered overflow so the pasted region exactly matches the
        # target canvas.
        left = max(0, (new_w - target_w) // 2)
        top = max(0, (new_h - target_h) // 2)
        scaled = scaled.crop((left, top, left + target_w, top + target_h))
        canvas.paste(scaled, (0, 0))
    else:
        off = ((target_w - new_w) // 2, (target_h - new_h) // 2)
        canvas.paste(scaled, off)

    return canvas


# ---------------------------------------------------------------------------
# Screen auto-detection.
# ---------------------------------------------------------------------------
# Order matters: most-common first. Each entry is a (code, class) tuple.
# All serial-based subclasses share the same constructor signature:
#     (com_port, display_width, display_height, update_queue)
# so we can pass them all the same way. LcdCommTuringUSB takes no args.
# We use the class object directly (no factory hack) so we can call the
# static `auto_detect_com_port()` cheaply.
SERIAL_CLASSES = [
    ("A",       LcdCommRevA),
    ("B",       LcdCommRevB),
    ("D",       LcdCommRevD),
    ("C",       LcdCommRevC),
    ("WEACT_A", LcdCommWeActA),
    ("WEACT_B", LcdCommWeActB),
]


def _instantiate_serial(cls, com_port: str, width: int, height: int) -> LcdComm:
    """Build any serial-based LcdComm subclass with a uniform signature."""
    return cls(
        com_port=com_port,
        display_width=width,
        display_height=height,
        update_queue=None,
    )


def detect_screen(revision_setting: str,
                  com_port_setting: str,
                  width: int = 320,
                  height: int = 480) -> Optional[LcdComm]:
    """
    Build an LcdComm instance.

    * If revision_setting is a concrete code (A/B/C/D/TUR_USB/WEACT_A/WEACT_B/SIMU),
      build that class directly.
    * If revision_setting is "AUTO", iterate SERIAL_CLASSES and return the
      first one whose `auto_detect_com_port()` finds a device, then try
      LcdCommTuringUSB (no COM port needed).
    * If nothing is found, fall back to LcdCommSimulated (window on host).
    """
    rev = (revision_setting or "AUTO").upper()

    # ----- explicit SIMU -----------------------------------------------------
    if rev == "SIMU":
        logger.info("Using SIMULATED display (no hardware). %dx%d", width, height)
        return LcdSimulated(display_width=width, display_height=height)

    # ----- explicit TUR_USB (USB HID, no COM port) ---------------------------
    if rev == "TUR_USB":
        if LcdCommTuringUSB is None:
            logger.error(
                "Cannot use TUR_USB revision: pyusb is not installed. "
                "Install with: pip install pyusb  (or your distro's python-pyusb)"
            )
            return None
        logger.info("Using revision TUR_USB (native USB HID).")
        try:
            lcd = LcdCommTuringUSB()
            lcd.InitializeComm()
            return lcd
        except Exception as exc:
            logger.error("Failed to open TUR_USB screen: %s", exc)
            return None

    # ----- explicit serial revision -----------------------------------------
    if rev in ("A", "B", "C", "D", "WEACT_A", "WEACT_B"):
        for code, cls in SERIAL_CLASSES:
            if code == rev:
                logger.info("Using configured revision %s (com_port=%s, %dx%d).",
                            rev, com_port_setting, width, height)
                try:
                    lcd = _instantiate_serial(cls, com_port_setting, width, height)
                    lcd.InitializeComm()
                    return lcd
                except Exception as exc:
                    logger.error("Failed to open %s screen: %s", rev, exc)
                    return None
        # unreachable
        return None

    # ----- AUTO detection ----------------------------------------------------
    logger.info("Auto-detecting smart screen...")
    for code, cls in SERIAL_CLASSES:
        try:
            detected_port = cls.auto_detect_com_port()
        except Exception:
            detected_port = None
        if detected_port:
            logger.info("Found device for revision %s on port %s",
                        code, detected_port)
            try:
                lcd = _instantiate_serial(cls, detected_port, width, height)
                lcd.InitializeComm()
                return lcd
            except Exception as exc:
                logger.warning("Detection matched %s but open failed: %s",
                               code, exc)
                continue

    # ----- last AUTO attempt: native USB HID (no COM port) ------------------
    if LcdCommTuringUSB is not None:
        try:
            lcd = LcdCommTuringUSB()
            lcd.InitializeComm()
            logger.info("Auto-detected revision TUR_USB (native USB HID).")
            return lcd
        except Exception as exc:
            logger.debug("TUR_USB auto-detect failed: %s", exc)
    else:
        logger.debug("Skipping TUR_USB auto-detect: pyusb not installed.")

    # ----- nothing detected: simulator --------------------------------------
    logger.warning(
        "No supported screen detected. Falling back to the SIMULATED "
        "display (a window will open on this host, %dx%d). If you have "
        "a screen attached, check the udev rules / dialout group "
        "membership, or force a specific revision in config.yaml.",
        width, height,
    )
    return LcdSimulated(display_width=width, display_height=height)


# ---------------------------------------------------------------------------
# Graceful shutdown.
# ---------------------------------------------------------------------------
class GracefulShutdown:
    """Flag set by SIGINT / SIGTERM handlers."""

    def __init__(self) -> None:
        self.stop_requested = False
        signal.signal(signal.SIGINT, self._handler)
        signal.signal(signal.SIGTERM, self._handler)

    def _handler(self, signum, frame):  # noqa: ANN001
        logger.info("Signal %s received; shutting down after current slide.",
                    signum)
        self.stop_requested = True

    def should_stop(self) -> bool:
        return self.stop_requested


# ---------------------------------------------------------------------------
# Main loop.
# ---------------------------------------------------------------------------
def run_slideshow(cfg: dict, once: bool = False) -> int:
    """Run the slideshow. Returns process exit code."""
    folder = resolve_images_folder(cfg["images_folder"])
    if not folder.is_dir():
        logger.error("Images folder does not exist: %s", folder)
        logger.error("Create it and put images inside, then edit config.yaml "
                     "if you want a different path.")
        return 2

    # Setup display
    disp_cfg = cfg["display"]
    width = int(disp_cfg.get("width") or 320)
    height = int(disp_cfg.get("height") or 480)
    lcd = detect_screen(
        disp_cfg["revision"],
        disp_cfg["com_port"],
        width=width,
        height=height,
    )
    if lcd is None:
        logger.error("Could not open any display.")
        return 3

    # Apply orientation & brightness.
    # Note: for rev. A screens, SetOrientation() can RESET the panel and
    # the COM port can briefly disappear. Wrap each call so a hang in one
    # doesn't kill the whole startup.
    try:
        orientation = ORIENTATION_MAP.get(
            disp_cfg["orientation"].upper(), Orientation.LANDSCAPE
        )
        logger.info("SetOrientation(%s) ...", orientation)
        lcd.SetOrientation(orientation)
        logger.info("SetOrientation done.")
    except Exception as exc:
        logger.warning("SetOrientation failed: %s", exc)

    # Explicitly turn the screen ON (some revisions start in 'off' state
    # after InitializeComm, especially on a restart).
    try:
        logger.info("ScreenOn() ...")
        lcd.ScreenOn()
        logger.info("ScreenOn done.")
    except Exception as exc:
        logger.warning("ScreenOn failed: %s", exc)

    try:
        logger.info("SetBrightness(%d) ...", int(disp_cfg["brightness"]))
        lcd.SetBrightness(int(disp_cfg["brightness"]))
        logger.info("SetBrightness done.")
    except Exception as exc:
        logger.warning("SetBrightness failed: %s", exc)

    sw, sh = lcd.get_width(), lcd.get_height()

    # Software rotation settings. When auto_orient is true, the rotation
    # is chosen per-image (landscape uses rotate_image, portrait uses
    # portrait_rotation). Either way, the per-image rotation determines
    # the pre-rotation canvas dimensions, which is why we no longer
    # pre-compute fit_w/fit_h here.
    base_rotate = int(cfg.get("rotate_image", 0)) % 360
    auto_orient = bool(cfg.get("auto_orient", False))
    portrait_rotate = int(cfg.get("portrait_rotation", 0)) % 360
    if auto_orient:
        logger.info("Auto-orient enabled: landscape images -> %d° CW, "
                    "portrait images -> %d° CW",
                    base_rotate, portrait_rotate)
    elif base_rotate:
        logger.info("Software rotation: always %d° CW", base_rotate)
    else:
        logger.info("Software rotation: none")

    # Video support
    videos_enabled = bool(cfg.get("videos_enabled", True))
    video_max_fps = max(1, min(60, int(cfg.get("video_max_fps", 24))))
    video_max_seconds = max(1, int(cfg.get("video_max_seconds", 30)))
    if videos_enabled and not HAS_CV2:
        logger.warning(
            "videos_enabled=true but OpenCV (cv2) is not installed. "
            "Videos will be skipped. Install with: pip install opencv-python "
            "(or your distro's python-opencv)"
        )
        videos_enabled = False
    elif videos_enabled:
        logger.info("Video playback enabled (max %d fps, %d s/video)",
                    video_max_fps, video_max_seconds)

    logger.info("Screen ready: %dx%d, fit=%s, interval=%ds, recursive=%s",
                sw, sh, cfg["fit_mode"], cfg["interval_seconds"],
                cfg["recursive"])

    bg = tuple(cfg["background_color"])  # (r,g,b)
    if len(bg) != 3:
        logger.warning("background_color must be 3 ints; defaulting to black.")
        bg = (0, 0, 0)

    # Initial clear so the previous theme isn't visible behind bars.
    try:
        logger.info("Clear() ...")
        lcd.Clear()
        logger.info("Clear done.")
    except Exception as exc:
        logger.warning("Initial Clear() failed (continuing): %s", exc)

    shutdown = GracefulShutdown()
    interval = max(1, int(cfg["interval_seconds"]))
    fit = cfg["fit_mode"]
    recursive = bool(cfg["recursive"])
    shuffle = bool(cfg["shuffle"])

    if shuffle:
        import random
        rng = random.Random()

    # ----- main loop --------------------------------------------------------
    while not shutdown.should_stop():
        # Re-scan every loop iteration -> hot-folder behaviour.
        files = collect_images(folder, recursive, include_videos=videos_enabled)
        if not files:
            logger.warning("No images/videos found in %s. Sleeping 10s then retrying.",
                           folder)
            # Show a placeholder so the user sees the screen is alive.
            _show_placeholder(lcd, sw, sh, "No images in folder", bg)
            _interruptible_sleep(10, shutdown)
            if once:
                return 2
            continue

        n_imgs = sum(1 for f in files if not is_video(f))
        n_vids = sum(1 for f in files if is_video(f))
        logger.info("Loaded %d file(s) from %s (%d images, %d videos)",
                    len(files), folder, n_imgs, n_vids)

        if shuffle:
            order = list(files)
            rng.shuffle(order)
        else:
            order = files

        for fpath in order:
            if shutdown.should_stop():
                break

            if is_video(fpath):
                if not videos_enabled:
                    continue
                _play_video(lcd, fpath, fit, bg,
                            base_rotate, auto_orient, portrait_rotate,
                            sw, sh,
                            video_max_fps, video_max_seconds,
                            shutdown)
            else:
                _show_image(lcd, fpath, fit, bg,
                            base_rotate, auto_orient, portrait_rotate,
                            sw, sh)

            if once:
                return 0

            # Only sleep for images; videos control their own timing.
            if not is_video(fpath):
                _interruptible_sleep(interval, shutdown)

        if once:
            return 0

    # ----- shutdown ---------------------------------------------------------
    # IMPORTANT: do NOT call ScreenOff() here. When running under systemd
    # with Restart=on-failure, the new instance starts before the screen
    # has fully reset, leaving the panel dark. Just close the serial port.
    logger.info("Slideshow stopping; closing serial port.")
    try:
        lcd.closeSerial()
    except Exception:
        pass
    return 0


def _show_placeholder(lcd: LcdComm, w: int, h: int,
                      text: str, bg: Tuple[int, int, int]) -> None:
    """Cheap placeholder: black screen + tiny text via DisplayText."""
    try:
        placeholder = Image.new("RGB", (w, h), bg)
        lcd.DisplayPILImage(placeholder, 0, 0, w, h)
    except Exception:
        pass


def _send_to_screen(lcd: LcdComm, fitted: Image.Image,
                    rotate_degrees: int,
                    panel_w: int, panel_h: int) -> None:
    """Apply software rotation (if any) and send the image to the panel.

    `fitted` is already sized to match the desired display orientation.
    After rotation, its dimensions should equal (panel_w, panel_h).
    """
    if rotate_degrees:
        fitted = rotate_image_software(fitted, rotate_degrees)
    # Always send with panel dimensions (the actual physical size).
    lcd.DisplayPILImage(fitted, 0, 0, panel_w, panel_h)


def _show_image(lcd: LcdComm, img_path: Path,
                fit_mode: str, bg: Tuple[int, int, int],
                base_rotate: int, auto_orient: bool, portrait_rotate: int,
                panel_w: int, panel_h: int) -> None:
    """Load, fit, optionally rotate, and display a single image."""
    try:
        with Image.open(img_path) as src:
            src.load()  # force read; allows file handle to close
        eff_rot = effective_rotation_for(src, base_rotate, auto_orient, portrait_rotate)
        fit_w, fit_h = fit_dims_for_rotation(eff_rot, panel_w, panel_h)
        fitted = fit_image(src, fit_w, fit_h, fit_mode, bg)
        logger.info("DisplayPILImage(%s) [src=%dx%d, rot=%d°, canvas=%dx%d] ...",
                    img_path.name, src.size[0], src.size[1],
                    eff_rot, fit_w, fit_h)
        _send_to_screen(lcd, fitted, eff_rot, panel_w, panel_h)
        logger.info("Showing %s done.", img_path.name)
    except Exception as exc:
        logger.error("Skipping image %s: %s", img_path, exc)


def _play_video(lcd: LcdComm, video_path: Path,
                fit_mode: str, bg: Tuple[int, int, int],
                base_rotate: int, auto_orient: bool, portrait_rotate: int,
                panel_w: int, panel_h: int,
                max_fps: int, max_seconds: int,
                shutdown: "GracefulShutdown") -> None:
    """Open a video with OpenCV and play its frames on the panel.

    Rotation/aspect detection is done once on the first frame (videos
    rarely change aspect mid-stream).
    """
    if not HAS_CV2:
        logger.warning("Skipping video %s (OpenCV not installed)", video_path)
        return
    cap = None
    try:
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            logger.error("Could not open video %s", video_path)
            return
        native_fps = cap.get(cv2.CAP_PROP_FPS) or max_fps
        fps = max(1, min(int(native_fps), max_fps))
        frame_delay = 1.0 / fps
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        max_frames = fps * max_seconds
        logger.info("Playing video %s (%.2f fps, capped to %d fps, ~%d frames, max %ds)",
                    video_path.name, native_fps, fps, total_frames, max_seconds)

        # Probe first frame to detect aspect ratio / decide rotation.
        ret, first_frame = cap.read()
        if not ret:
            logger.error("Video %s has no frames", video_path)
            return
        fh, fw = first_frame.shape[:2]
        # Build a throwaway PIL image just to use our helpers.
        probe = Image.fromarray(cv2.cvtColor(first_frame, cv2.COLOR_BGR2RGB))
        eff_rot = effective_rotation_for(probe, base_rotate, auto_orient, portrait_rotate)
        fit_w, fit_h = fit_dims_for_rotation(eff_rot, panel_w, panel_h)
        logger.info("Video %s aspect %dx%d -> rotation %d°, canvas %dx%d",
                    video_path.name, fw, fh, eff_rot, fit_w, fit_h)

        # Show the first frame.
        frame_rgb = cv2.cvtColor(first_frame, cv2.COLOR_BGR2RGB)
        pil_img = Image.fromarray(frame_rgb)
        fitted = fit_image(pil_img, fit_w, fit_h, fit_mode, bg)
        _send_to_screen(lcd, fitted, eff_rot, panel_w, panel_h)

        frame_idx = 1
        next_frame_time = time.monotonic() + frame_delay
        while not shutdown.should_stop():
            ret, frame = cap.read()
            if not ret:
                break  # end of video
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            fitted = fit_image(pil_img, fit_w, fit_h, fit_mode, bg)
            _send_to_screen(lcd, fitted, eff_rot, panel_w, panel_h)
            frame_idx += 1
            if frame_idx >= max_frames:
                logger.info("Video reached max time (%ds), stopping", max_seconds)
                break
            # Pace to the target FPS so we don't burn CPU.
            now = time.monotonic()
            if next_frame_time > now:
                _interruptible_sleep(int(next_frame_time - now) or 1, shutdown)
            else:
                # We're behind; resync to now to avoid catch-up bursts.
                next_frame_time = now
            next_frame_time += frame_delay
        cap.release()
        logger.info("Video %s done (%d frames shown)", video_path.name, frame_idx)
    except Exception as exc:
        logger.error("Skipping video %s: %s", video_path, exc)
        try:
            if cap is not None:
                cap.release()
        except Exception:
            pass


def _interruptible_sleep(seconds: int, shutdown: GracefulShutdown) -> None:
    """Sleep in 1-second chunks so SIGINT/SIGTERM respond quickly."""
    end = time.monotonic() + seconds
    while time.monotonic() < end and not shutdown.should_stop():
        time.sleep(min(1.0, end - time.monotonic()))


# ---------------------------------------------------------------------------
# CLI entry point.
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Display an image slideshow on a turing-smart-screen-python "
                    "supported USB-C smart screen.",
    )
    parser.add_argument(
        "--config", "-c",
        default=str(SCRIPT_DIR / "config.yaml"),
        help="Path to config.yaml (default: ./config.yaml next to this script).",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Show the first image only (useful for testing config).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Verbose logging (DEBUG level).",
    )
    args = parser.parse_args()

    cfg = load_config(Path(args.config))
    level_name = "DEBUG" if args.verbose else str(cfg.get("log_level", "INFO")).upper()
    # `force=True` is critical: the parent turing-smart-screen-python
    # library (when it gets imported via `from library.lcd...`) installs
    # its own logging handlers, which would otherwise make our
    # basicConfig a no-op and silently swallow all of *our* log lines
    # (Screen ready, Showing X.jpg, etc.). force=True makes us win.
    logging.basicConfig(
        level=getattr(logging, level_name, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        force=True,
    )

    logger.info("turing-slideshow starting up.")
    logger.info("Config file: %s", args.config)
    logger.info("Repo root:   %s", REPO_ROOT)

    return run_slideshow(cfg, once=args.once)


if __name__ == "__main__":
    sys.exit(main())
