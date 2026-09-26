#!/usr/bin/env python3
"""Step 3 helper: find and configure a USB webcam on the Modalix DevKit.

Runs `v4l2-ctl` (already on the board) to enumerate video devices, filters out the
internal ISP/stats nodes (arm-isp-*, modalix-isp-stats-*) that always show up on this
SoC, and reports only devices that look like a real UVC webcam. For the chosen
device, lists its supported pixel formats/resolutions/framerates and recommends one,
following the same reasoning as demo-neat/apps/usb-camera-yolo26m/LEARNING.md: prefer
MJPG (raw YUYV is USB2-bandwidth-limited and drops fps hard at higher resolutions).

This must run ON THE DEVKIT (it shells out to v4l2-ctl/lsusb), so invoke it via `dk`:

    dk ./probe_camera.py                                   # list candidate devices
    dk ./probe_camera.py --device /dev/video16              # list its formats
    dk ./probe_camera.py --device /dev/video16 --apply      # write choice into config

`--apply` picks the highest-resolution MJPG mode (or the highest-fps mode at that
resolution) and rewrites camera_device/width/height/fps in the target --config file
(default: config/default.conf), leaving every other line untouched.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent

# Internal SiMa ISP/stats video nodes that always exist on this SoC and are never a
# USB webcam -- filtered out so the candidate list stays short and relevant.
_INTERNAL_PATTERNS = ("arm-isp", "modalix-isp-stats")


def run(cmd: list[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    except FileNotFoundError:
        raise SystemExit(f"'{cmd[0]}' not found on this device")
    return r.stdout


def list_v4l2_groups() -> list[tuple[str, list[str]]]:
    """Parse `v4l2-ctl --list-devices` into (driver_label, [device_paths]) groups."""
    text = run(["v4l2-ctl", "--list-devices"])
    groups: list[tuple[str, list[str]]] = []
    label = None
    paths: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            if label is not None:
                groups.append((label, paths))
            label = line.strip()
            paths = []
        else:
            paths.append(line.strip())
    if label is not None:
        groups.append((label, paths))
    return groups


def candidate_devices() -> list[tuple[str, list[str]]]:
    return [
        (label, paths) for label, paths in list_v4l2_groups()
        if not any(pat in label for pat in _INTERNAL_PATTERNS)
    ]


def list_usb_devices() -> str:
    return run(["lsusb"])


def list_formats(device: str) -> str:
    return run(["v4l2-ctl", "-d", device, "--list-formats-ext"])


def parse_mjpg_modes(formats_text: str) -> list[tuple[int, int, float]]:
    """Extract (width, height, max_fps) triples for the MJPG/MJPEG format block."""
    modes = []
    in_mjpg = False
    width = height = None
    for line in formats_text.splitlines():
        stripped = line.strip()
        if re.match(r"^\[\d+\]:\s*'(MJPG|MJPEG)'", stripped):
            in_mjpg = True
            continue
        if re.match(r"^\[\d+\]:", stripped):
            in_mjpg = False
            continue
        if not in_mjpg:
            continue
        m = re.match(r"Size:\s*Discrete\s+(\d+)x(\d+)", stripped)
        if m:
            width, height = int(m.group(1)), int(m.group(2))
            continue
        m = re.search(r"\(([\d.]+)\s*fps\)", stripped)
        if m and width is not None:
            modes.append((width, height, float(m.group(1))))
    return modes


def recommend(modes: list[tuple[int, int, float]]) -> tuple[int, int, int] | None:
    if not modes:
        return None
    by_res: dict[tuple[int, int], float] = {}
    for w, h, fps in modes:
        by_res[(w, h)] = max(by_res.get((w, h), 0.0), fps)
    (w, h), fps = max(by_res.items(), key=lambda kv: (kv[0][0] * kv[0][1], kv[1]))
    return w, h, int(round(fps))


def apply_to_config(config_path: Path, device: str, width: int, height: int, fps: int) -> None:
    lines = config_path.read_text(encoding="utf-8").splitlines(keepends=True)
    targets = {"camera_device": device, "width": str(width), "height": str(height), "fps": str(fps)}
    seen = set()
    out = []
    for line in lines:
        stripped = line.split("#", 1)[0].strip()
        if "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in targets:
                out.append(f"{key}={targets[key]}\n")
                seen.add(key)
                continue
        out.append(line)
    missing = set(targets) - seen
    if missing:
        raise SystemExit(f"{config_path}: missing expected keys {sorted(missing)}, refusing to guess placement")
    config_path.write_text("".join(out), encoding="utf-8")
    print(f"[apply] wrote {targets} into {config_path}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="device path to inspect (e.g. /dev/video16); omit to just list candidates")
    ap.add_argument("--apply", action="store_true", help="write the recommended mode into --config")
    ap.add_argument("--config", type=Path, default=APP_DIR / "config" / "default.conf")
    args = ap.parse_args()

    if args.device is None:
        print("=== lsusb (look for your camera's vendor, not just 'Hub') ===")
        print(list_usb_devices())
        print("=== candidate video devices (internal ISP/stats nodes filtered out) ===")
        groups = candidate_devices()
        if not groups:
            print("No non-internal /dev/video* devices found. Is the USB webcam plugged in?")
            print("(Every device on this board showed up as an internal arm-isp-*/modalix-isp-stats-* node.)")
            return 1
        for label, paths in groups:
            print(f"{label}:")
            for p in paths:
                print(f"  {p}")
        print("\nRe-run with --device <path> (usually the lowest-numbered 'Video Capture' node,")
        print("not a '...meta' one) to see its supported formats.")
        return 0

    print(f"=== formats for {args.device} ===")
    formats_text = list_formats(args.device)
    print(formats_text)

    modes = parse_mjpg_modes(formats_text)
    if not modes:
        print("[warn] no MJPG/MJPEG mode found. usb-camera-yolo26m/LEARNING.md found raw YUYV")
        print("       bandwidth-limited on USB2 (5 fps at 1080p) -- expect similar here if you")
        print("       have to fall back to a different format.")
        return 1

    rec = recommend(modes)
    assert rec is not None
    w, h, fps = rec
    print(f"\n[recommend] MJPG {w}x{h}@{fps} (highest resolution, then highest fps at that resolution)")

    if args.apply:
        apply_to_config(args.config, args.device, w, h, fps)
    else:
        print(f"Re-run with --apply to write this into {args.config}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
