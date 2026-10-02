#!/usr/bin/env python3
"""kmssink force-modesetting で 1280x720 に切り替えられるかの確認(エラー詳細を表示)"""
import subprocess, time
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
subprocess.run(["sudo", "-n", "systemctl", "stop", "lightdm"])
try:
    for w, h in ((1280, 720),):
        p = Gst.parse_launch(f"videotestsrc num-buffers=60 ! video/x-raw,format=BGRx,width={w},height={h},framerate=15/1 ! "
                             f"kmssink driver-name=smifb sync=false skip-vsync=true force-modesetting=true")
        ret = p.set_state(Gst.State.PLAYING)
        msg = p.get_bus().timed_pop_filtered(6 * Gst.SECOND, Gst.MessageType.ERROR | Gst.MessageType.EOS)
        if msg and msg.type == Gst.MessageType.ERROR:
            err, dbg = msg.parse_error()
            print(f"{w}x{h}: set_state={ret.value_nick} ERROR {err.message} | {dbg}")
        else:
            print(f"{w}x{h}: set_state={ret.value_nick} {'EOS (ok)' if msg else 'no message'}")
        p.set_state(Gst.State.NULL)
finally:
    subprocess.run(["sudo", "-n", "systemctl", "start", "lightdm"])
    r = subprocess.run("cat /sys/class/drm/card*-HDMI*/modes 2>/dev/null | head -20", shell=True, capture_output=True, text=True)
    print("connector modes:", r.stdout.split())
