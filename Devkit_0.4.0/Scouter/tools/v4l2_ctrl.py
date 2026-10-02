#!/usr/bin/env python3
"""v4l2-ctl ラッパー (dkはシェルスクリプトを直接実行できないため)
   例: dk ./tools/v4l2_ctrl.py --list-ctrls-menus
       dk ./tools/v4l2_ctrl.py --set-ctrl=auto_exposure=1,exposure_time_absolute=150"""
import subprocess, sys
dev = "/dev/video0"
args = sys.argv[1:]
if args and args[0].startswith("/dev/"):
    dev, args = args[0], args[1:]
r = subprocess.run(["v4l2-ctl", "-d", dev] + args, capture_output=True, text=True)
print(r.stdout, r.stderr)
