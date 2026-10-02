#!/usr/bin/env python3
"""USB接続のカメラ: 接続速度(Mbps)とドライバ実測fps(v4l2-ctl --stream-mmap)"""
import glob, os, subprocess, sys
dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"
for d in glob.glob("/sys/bus/usb/devices/*"):
    try:
        prod = open(os.path.join(d, "product")).read().strip()
    except OSError:
        continue
    speed = open(os.path.join(d, "speed")).read().strip()
    ver = open(os.path.join(d, "version")).read().strip()
    print(f"{os.path.basename(d):8s} speed={speed}Mbps usb={ver} {prod}")
for fmt in ("MJPG", "H264"):
    subprocess.run(["v4l2-ctl", "-d", dev, f"--set-fmt-video=width=1280,height=720,pixelformat={fmt}",
                    "--set-parm=30"], capture_output=True)
    r = subprocess.run(["v4l2-ctl", "-d", dev, "--stream-mmap", "--stream-count=120"],
                       capture_output=True, text=True, timeout=60)
    lines = [l for l in (r.stdout + r.stderr).replace("<", "\n").split("\n") if "fps" in l]
    print(fmt, "v4l2 stream:", lines[-3:] if lines else (r.stdout + r.stderr)[-300:])
r = subprocess.run(["v4l2-ctl", "-d", dev, "--get-parm"], capture_output=True, text=True)
print(r.stdout)
