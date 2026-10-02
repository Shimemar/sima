#!/usr/bin/env python3
"""HDMI出力の単体テスト: 5秒間テストパターン(動く帯+文字)をDevKitのHDMIに出す。
lightdmを一時停止し、終了時に再開する。  dk ./tools/hdmi_test.py [秒]"""
import glob, sys, time
from pathlib import Path
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
import pyneat  # noqa: F401 - load pyneat first, as main.py does, to catch GStreamer conflicts
from hdmi_out import HdmiOutput

secs = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0
fps = float(sys.argv[2]) if len(sys.argv) > 2 else 15.0
from sysmon import SysMon
mon = SysMon(Path(__file__).resolve().parents[1] / "tools" / "sysmon_hdmi_test.jsonl",
             extra=lambda: {"hdmi_frames": out.frames}).start()
out = HdmiOutput()
try:
    w, h = out.start()
    print(f"HDMI display {w}x{h}", flush=True)
    t0 = time.perf_counter()
    push_ms = []
    while time.perf_counter() - t0 < secs:
        f = np.zeros((720, 1280, 3), np.uint8)
        x = int((time.perf_counter() - t0) / secs * 1280)
        f[:, max(0, x - 60):x] = (90, 255, 90)
        cv2.putText(f, f"SCOUTER HDMI TEST {time.perf_counter() - t0:4.1f}s", (60, 360),
                    cv2.FONT_HERSHEY_DUPLEX, 2.0, (255, 255, 255), 3)
        t1 = time.perf_counter()
        out.push(f)
        push_ms.append((time.perf_counter() - t1) * 1000)
        time.sleep(1 / fps)
        if out.error:
            break
    print(f"frames={out.frames} push mean={sum(push_ms) / len(push_ms):.1f}ms max={max(push_ms):.1f}ms "
          f"error={out.error}", flush=True)
finally:
    out.close()
    mon.close()
    import subprocess
    st = subprocess.run(["systemctl", "is-active", "lightdm"], capture_output=True, text=True).stdout.strip()
    print(f"lightdm after close: {st}", flush=True)
