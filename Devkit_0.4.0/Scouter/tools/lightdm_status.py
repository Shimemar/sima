#!/usr/bin/env python3
"""lightdm(デスクトップ)の状態確認。止まったままなら --start で再開。 dk ./tools/lightdm_status.py [--start]"""
import subprocess, sys
if "--start" in sys.argv:
    subprocess.run(["sudo", "-n", "systemctl", "start", "lightdm"], check=False)
print("lightdm:", subprocess.run(["systemctl", "is-active", "lightdm"], capture_output=True, text=True).stdout.strip())
