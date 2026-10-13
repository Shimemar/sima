#!/usr/bin/env python3
"""USBカメラの実フレームレートと、前処理(resize+正規化)の所要時間を測る（DevKitで実行）。

    dk ./tools/camera_fps.py [--device /dev/video0] [--seconds 5]
暗い部屋では UVC カメラが露光時間を延ばして 30fps を出さないことがある（深度fpsの上限になる）。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from camera import Camera  # noqa: E402
from depth import preprocess  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--device', default='/dev/video0')
ap.add_argument('--seconds', type=float, default=5)
ap.add_argument('--size', default='1280x720')
args = ap.parse_args()
w, h = map(int, args.size.split('x'))
cam = Camera(args.device, w, h)
frame, last = cam.latest_stamped()
n, t0, pre = 0, time.time(), []
while time.time() - t0 < args.seconds:
    f, s = cam.latest_stamped()
    if s != last:
        last = s
        n += 1
        t = time.perf_counter()
        preprocess(f)
        pre.append((time.perf_counter() - t) * 1000)
    time.sleep(0.001)
cam.stop()
print(f'camera {frame.shape[1]}x{frame.shape[0]}: {n / args.seconds:.1f} fps, '
      f'preprocess mean {sum(pre) / max(1, len(pre)):.1f} ms')
