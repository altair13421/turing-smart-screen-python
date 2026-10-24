#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Standalone smoke test for turing-slideshow's image-fitting logic.
# This test does NOT require the parent turing-smart-screen-python
# library to be installed - it stubs out the library imports so we can
# verify the image scaling math in isolation.
#
# Run:
#   python3 tests/test_fit_image.py
#
# Exits 0 on success, non-zero on failure.
"""
Standalone test for turing-slideshow.fit_image().

We monkey-patch sys.modules so that importing `slideshow` succeeds
without needing the parent turing-smart-screen-python library. Then we
exercise fit_image() with synthetic images of various sizes and verify
the output canvas has the right dimensions and the source image is
positioned correctly for each fit_mode.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

# Ensure we can import the slideshow module from its parent dir.
SCRIPT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPT_DIR))

# ---- Create a fake parent repo on disk so slideshow.py's path-bootstrap
# check passes (it looks for library/lcd/lcd_comm.py relative to the
# parent of turing-slideshow/). Then point TURING_SMART_SCREEN_ROOT at it.
_FAKE_REPO = Path(tempfile.mkdtemp(prefix="fake_turing_repo_"))
(_FAKE_REPO / "library" / "lcd").mkdir(parents=True, exist_ok=True)
(_FAKE_REPO / "library" / "lcd" / "lcd_comm.py").write_text("# stub\n")
os.environ["TURING_SMART_SCREEN_ROOT"] = str(_FAKE_REPO)

# ---- stub out the parent library imports so slideshow.py loads ---------
# slideshow.py imports these at module load time:
#   from library.lcd.lcd_comm import LcdComm, Orientation
#   from library.lcd.lcd_comm_rev_a import LcdCommRevA
#   ... etc.
# We replace each with a dummy module that has the symbols referenced.


class _DummyOrientation:
    PORTRAIT = 0
    LANDSCAPE = 2
    REVERSE_PORTRAIT = 1
    REVERSE_LANDSCAPE = 3


def _make_dummy_lcd_module(name):
    mod = types.ModuleType(name)

    class _DummyLcdComm:
        def __init__(self, *a, **kw):
            pass

    mod.LcdComm = _DummyLcdComm
    mod.Orientation = _DummyOrientation
    return mod


def _make_dummy_subclass_module(name, classname):
    mod = types.ModuleType(name)

    class _Dummy:
        def __init__(self, *a, **kw):
            pass

        @staticmethod
        def auto_detect_com_port():
            return None

    mod.__dict__[classname] = _Dummy
    return mod


_LCD_SUBCLASSES = [
    ("library.lcd.lcd_comm_rev_a", "LcdCommRevA"),
    ("library.lcd.lcd_comm_rev_b", "LcdCommRevB"),
    ("library.lcd.lcd_comm_rev_c", "LcdCommRevC"),
    ("library.lcd.lcd_comm_rev_d", "LcdCommRevD"),
    ("library.lcd.lcd_comm_turing_usb", "LcdCommTuringUSB"),
    ("library.lcd.lcd_comm_weact_a", "LcdCommWeActA"),
    ("library.lcd.lcd_comm_weact_b", "LcdCommWeActB"),
    ("library.lcd.lcd_simulated", "LcdSimulated"),
]

# Build the nested `library` and `library.lcd` packages first.
for pkg_path in ["library", "library.lcd"]:
    if pkg_path not in sys.modules:
        pkg = types.ModuleType(pkg_path)
        pkg.__path__ = []
        sys.modules[pkg_path] = pkg

# Stub `library.log` (referenced indirectly).
if "library.log" not in sys.modules:
    import logging
    logmod = types.ModuleType("library.log")
    logmod.logger = logging.getLogger("dummy")
    sys.modules["library.log"] = logmod

# Stub `library.lcd.lcd_comm` with LcdComm + Orientation.
sys.modules["library.lcd.lcd_comm"] = _make_dummy_lcd_module("library.lcd.lcd_comm")

# Stub each subclass module.
for modpath, classname in _LCD_SUBCLASSES:
    sys.modules[modpath] = _make_dummy_subclass_module(modpath, classname)

# Now we can import the slideshow module.
import slideshow  # noqa: E402

from PIL import Image  # noqa: E402

# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------
PASS = 0
FAIL = 0


def check(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}")


def make_image(w, h, color=(255, 128, 64)):
    """A solid-color image."""
    return Image.new("RGB", (w, h), color)


print("\n=== fit_image tests ===")
print("Target screen: 480 x 320 (landscape 3.5\")")

TARGET_W, TARGET_H = 480, 320
BG = (0, 0, 0)

# --- contain mode ---
print("\n[contain] 16:9 source (1920x1080) -> should fit width, letterbox")
src = make_image(1920, 1080)
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "contain", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))
check("letterbox top is black", out.getpixel((10, 10)) == BG)
check("letterbox bottom is black", out.getpixel((10, TARGET_H - 10)) == BG)
check("middle row has image", out.getpixel((TARGET_W // 2, TARGET_H // 2)) != BG)

print("\n[contain] 1:1 square source (500x500) -> should fit height, pillarbox")
src = make_image(500, 500)
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "contain", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))
check("left pillar is black", out.getpixel((10, TARGET_H // 2)) == BG)
check("right pillar is black", out.getpixel((TARGET_W - 10, TARGET_H // 2)) == BG)
check("center has image", out.getpixel((TARGET_W // 2, TARGET_H // 2)) != BG)

# --- cover mode ---
print("\n[cover] 16:9 source (1920x1080) -> should fill, crop horizontally")
src = make_image(1920, 1080)
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "cover", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))
check("no black bars (left)", out.getpixel((10, TARGET_H // 2)) != BG)
check("no black bars (right)", out.getpixel((TARGET_W - 10, TARGET_H // 2)) != BG)

# --- stretch mode ---
print("\n[stretch] 100x400 source -> should distort to 480x320")
src = make_image(100, 400)
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "stretch", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))
check("no black bars anywhere", out.getpixel((10, 10)) != BG)

# --- native mode ---
print("\n[native] small source 200x200 -> centered, no scaling, black bars")
src = make_image(200, 200, color=(50, 100, 150))
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "native", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))
expected_center = (50, 100, 150)
check("center has source color",
      out.getpixel((TARGET_W // 2, TARGET_H // 2)) == expected_center)
check("corner is black", out.getpixel((5, 5)) == BG)

print("\n[native] oversized source 800x800 -> falls back to contain")
src = make_image(800, 800)
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "native", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))
# 800x800 scaled to fit 480x320 -> 320x320, centered horizontally:
# the image fills the full height (320) but leaves left/right pillars.
check("left pillar is black", out.getpixel((5, TARGET_H // 2)) == BG)
check("right pillar is black", out.getpixel((TARGET_W - 5, TARGET_H // 2)) == BG)

# --- non-RGB input ---
print("\n[non-RGB] RGBA source with alpha -> converted to RGB")
src = Image.new("RGBA", (100, 100), (255, 0, 0, 128))
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "contain", BG)
check("output mode is RGB", out.mode == "RGB")
check("output size == target", out.size == (TARGET_W, TARGET_H))

# --- zero-dimension source should raise ---
print("\n[zero-size] 0x0 source -> raises ValueError")
src = Image.new("RGB", (0, 0))
try:
    slideshow.fit_image(src, TARGET_W, TARGET_H, "contain", BG)
    check("raises on zero size", False)
except ValueError:
    check("raises on zero size", True)

# --- unknown fit_mode falls back to contain ---
print("\n[unknown mode] -> falls back to contain, no crash")
src = make_image(640, 480)
out = slideshow.fit_image(src, TARGET_W, TARGET_H, "bogus_mode", BG)
check("output size == target", out.size == (TARGET_W, TARGET_H))


# ---------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------
print("\n=== config loading tests ===")

import tempfile

with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    f.write("""
images_folder: /tmp/test_images
interval_seconds: 7
shuffle: true
recursive: false
fit_mode: cover
background_color: [10, 20, 30]
display:
  revision: B
  com_port: /dev/ttyACM2
  orientation: PORTRAIT
  brightness: 50
  width: 480
  height: 800
log_level: DEBUG
""")
    cfg_path = f.name

cfg = slideshow.load_config(Path(cfg_path))
check("override interval_seconds", cfg["interval_seconds"] == 7)
check("override shuffle", cfg["shuffle"] is True)
check("override recursive", cfg["recursive"] is False)
check("override fit_mode", cfg["fit_mode"] == "cover")
check("override background_color", tuple(cfg["background_color"]) == (10, 20, 30))
check("override display.revision", cfg["display"]["revision"] == "B")
check("override display.com_port", cfg["display"]["com_port"] == "/dev/ttyACM2")
check("override display.width", cfg["display"]["width"] == 480)
check("override display.height", cfg["display"]["height"] == 800)
check("override display.brightness", cfg["display"]["brightness"] == 50)

# Missing config file -> defaults applied, no crash.
cfg = slideshow.load_config(Path("/nonexistent/path/config.yaml"))
check("defaults applied when config missing",
      cfg["interval_seconds"] == 30 and
      cfg["fit_mode"] == "contain" and
      cfg["display"]["revision"] == "AUTO")

# Malformed YAML -> defaults applied, no crash.
with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
    f.write("this: is: not: valid: yaml: [\n")
    bad_cfg_path = f.name

cfg = slideshow.load_config(Path(bad_cfg_path))
check("defaults applied on malformed YAML",
      cfg["interval_seconds"] == 30)

os.unlink(cfg_path)
os.unlink(bad_cfg_path)


# ---------------------------------------------------------------------
# Software rotation tests
# ---------------------------------------------------------------------
print("\n=== software rotation tests ===")

# 0° should be a no-op (same object, not even copied)
src = make_image(100, 50, (10, 20, 30))
out = slideshow.rotate_image_software(src, 0)
check("0° returns same image", out is src)

# 90° CW: a 100x50 image becomes 50x100
out = slideshow.rotate_image_software(src, 90)
check("90° swaps dimensions", out.size == (50, 100))

# 270° CW (= 90° CCW): also swaps dimensions
out = slideshow.rotate_image_software(src, 270)
check("270° swaps dimensions", out.size == (50, 100))

# 180°: dimensions preserved
out = slideshow.rotate_image_software(src, 180)
check("180° preserves dimensions", out.size == (100, 50))

# 360° (= 0°): no-op
out = slideshow.rotate_image_software(src, 360)
check("360° treated as 0° (no-op)", out is src)

# Color preservation: a known-colored pixel should still be present after rotation
src = make_image(10, 10, (0, 0, 0))
src.putpixel((0, 0), (255, 0, 0))  # top-left red
out_90 = slideshow.rotate_image_software(src, 90)
# After 90° CW, top-left pixel goes to top-right
check("90° CW: top-left red moves to top-right",
      out_90.getpixel((9, 0)) == (255, 0, 0))
out_180 = slideshow.rotate_image_software(src, 180)
# After 180°, top-left goes to bottom-right
check("180°: top-left red moves to bottom-right",
      out_180.getpixel((9, 9)) == (255, 0, 0))
out_270 = slideshow.rotate_image_software(src, 270)
# After 270° CW (= 90° CCW), top-left goes to bottom-left
check("270° CW: top-left red moves to bottom-left",
      out_270.getpixel((0, 9)) == (255, 0, 0))


# ---------------------------------------------------------------------
# Auto-orient tests
# ---------------------------------------------------------------------
print("\n=== auto-orient tests ===")

# is_landscape helper
check("is_landscape(800x600) -> True",
      slideshow.is_landscape(make_image(800, 600)))
check("is_landscape(600x800) -> False",
      not slideshow.is_landscape(make_image(600, 800)))
check("is_landscape(500x500 square) -> True (>=, edge case)",
      slideshow.is_landscape(make_image(500, 500)))

# effective_rotation_for: when auto_orient is OFF, always use base_rotation
src_landscape = make_image(800, 600)
src_portrait  = make_image(600, 800)
check("auto_orient=False: landscape uses base_rotate",
      slideshow.effective_rotation_for(src_landscape, base_rotation=270,
                                        auto_orient=False, portrait_rotation=90)
      == 270)
check("auto_orient=False: portrait also uses base_rotate",
      slideshow.effective_rotation_for(src_portrait, base_rotation=270,
                                        auto_orient=False, portrait_rotation=90)
      == 270)

# effective_rotation_for: when auto_orient is ON, pick per-image
check("auto_orient=True: landscape uses base_rotate",
      slideshow.effective_rotation_for(src_landscape, base_rotation=270,
                                        auto_orient=True, portrait_rotation=90)
      == 270)
check("auto_orient=True: portrait uses portrait_rotate",
      slideshow.effective_rotation_for(src_portrait, base_rotation=270,
                                        auto_orient=True, portrait_rotation=90)
      == 90)
check("auto_orient=True: portrait_rotation=0 leaves portrait upright",
      slideshow.effective_rotation_for(src_portrait, base_rotation=270,
                                        auto_orient=True, portrait_rotation=0)
      == 0)

# fit_dims_for_rotation: the canvas dims swap when rotating 90/270
check("fit_dims for 0°   on 320x480 panel -> 320x480",
      slideshow.fit_dims_for_rotation(0,   320, 480) == (320, 480))
check("fit_dims for 180° on 320x480 panel -> 320x480",
      slideshow.fit_dims_for_rotation(180, 320, 480) == (320, 480))
check("fit_dims for 90°  on 320x480 panel -> 480x320 (swapped)",
      slideshow.fit_dims_for_rotation(90,  320, 480) == (480, 320))
check("fit_dims for 270° on 320x480 panel -> 480x320 (swapped)",
      slideshow.fit_dims_for_rotation(270, 320, 480) == (480, 320))


# ---------------------------------------------------------------------
# Folder scanning tests
# ---------------------------------------------------------------------
print("\n=== folder scanning tests ===")

with tempfile.TemporaryDirectory() as tmpdir:
    root = Path(tmpdir)
    (root / "a.jpg").write_bytes(b"x")
    (root / "b.PNG").write_bytes(b"x")
    (root / "c.txt").write_bytes(b"x")  # should be skipped
    (root / "v.mp4").write_bytes(b"x")  # video, not picked up by default
    (root / "sub1").mkdir()
    (root / "sub1" / "d.gif").write_bytes(b"x")
    (root / "sub1" / "sub2").mkdir()
    (root / "sub1" / "sub2" / "e.WEBP").write_bytes(b"x")
    (root / "sub1" / "sub2" / "f.MOV").write_bytes(b"x")  # video in subfolder

    imgs = slideshow.collect_images(root, recursive=True)
    names = sorted(p.name for p in imgs)
    check("recursive, no videos: 4 images found (a, b, d, e)",
          names == ["a.jpg", "b.PNG", "d.gif", "e.WEBP"])

    all_files = slideshow.collect_images(root, recursive=True, include_videos=True)
    names = sorted(p.name for p in all_files)
    check("recursive, with videos: 6 files found (a, b, d, e, f.MOV, v.mp4)",
          names == ["a.jpg", "b.PNG", "d.gif", "e.WEBP", "f.MOV", "v.mp4"])

    # is_video() helper
    check("is_video(.mp4) -> True", slideshow.is_video(Path("foo.mp4")))
    check("is_video(.MP4) -> True (case-insensitive)", slideshow.is_video(Path("foo.MP4")))
    check("is_video(.jpg) -> False", not slideshow.is_video(Path("foo.jpg")))
    check("is_video(.txt) -> False", not slideshow.is_video(Path("foo.txt")))

    # Order should be alphabetical (lowercased path)
    imgs = slideshow.collect_images(root, recursive=True)
    paths = [p.as_posix().lower() for p in imgs]
    check("sorted alphabetically", paths == sorted(paths))


# ---------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------
print(f"\n=== Summary: {PASS} passed, {FAIL} failed ===")
sys.exit(0 if FAIL == 0 else 1)

