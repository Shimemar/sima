#!/usr/bin/env python3
"""カメラ単体のfps測定: 対応フォーマット一覧 + 解像度/露出ごとの実測fps"""
import glob, subprocess, sys, time
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
import cv2

dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"
for args in ([] if len(sys.argv) > 2 else [["--list-formats-ext"]]):
    try:
        print(subprocess.run(["v4l2-ctl", "-d", dev] + args, capture_output=True, text=True, timeout=10).stdout)
    except Exception as e:
        print("v4l2-ctl failed:", e)

def measure(w, h, fourcc="MJPG", seconds=4.0, decode=True):
    cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
    cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*fourcc))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, w); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
    cap.set(cv2.CAP_PROP_FPS, 30)
    for _ in range(10):
        cap.read()
    n, t0 = 0, time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        ok = cap.grab() if not decode else cap.read()[0]
        n += ok
    fps = n / (time.perf_counter() - t0)
    aw, ah = cap.get(cv2.CAP_PROP_FRAME_WIDTH), cap.get(cv2.CAP_PROP_FRAME_HEIGHT)
    cap.release()
    print(f"{fourcc} {int(aw)}x{int(ah)} decode={decode}: {fps:.1f} fps")

modes = sys.argv[2:] or ["MJPG:1280x720", "MJPG:640x480"]
for m in modes:
    fourcc, size = m.split(":")
    w, h = map(int, size.split("x"))
    measure(w, h, fourcc=fourcc, decode=False)
