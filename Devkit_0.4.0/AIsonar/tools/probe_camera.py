#!/usr/bin/env python3
"""List V4L2 devices and MJPEG modes on the DevKit (run via: dk ./tools/probe_camera.py)."""
import subprocess

for cmd in (["v4l2-ctl", "--list-devices"],):
    print("$", " ".join(cmd))
    print(subprocess.run(cmd, capture_output=True, text=True).stdout)
out = subprocess.run(["v4l2-ctl", "--list-devices"], capture_output=True, text=True).stdout
for line in out.splitlines():
    dev = line.strip()
    if dev.startswith("/dev/video") and not any(k in out.split(dev)[0].splitlines()[-1] for k in ("arm-isp", "sima")):
        r = subprocess.run(["v4l2-ctl", "-d", dev, "--list-formats-ext"], capture_output=True, text=True).stdout
        if "MJPG" in r:
            print(f"== {dev} (MJPEG capable)")
            print("\n".join(l for l in r.splitlines() if "Size" in l or "MJPG" in l or "YUYV" in l)[:1500])
r = subprocess.run(["pgrep", "-af", "python3"], capture_output=True, text=True).stdout
print("python processes:\n" + r)
