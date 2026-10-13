#!/usr/bin/env python3
"""MLA量子化キャリブレーション用に、USBカメラ画像を calib/ へ保存する（DevKitで実行）。

    dk ./tools/collect_calib.py --count 60 --interval 0.5
撮影中にカメラの向きを変え、近い物・遠い物・壁・人などが写るようにすると代表性が上がる。
画像はモデル入力と同じく 256x256 に引き伸ばして保存する（アスペクト比は保たない: 本番の前処理と同じ）。
"""
import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from camera import Camera, cv2  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument('--device', default='/dev/video0')
ap.add_argument('--count', type=int, default=60)
ap.add_argument('--interval', type=float, default=0.5)
ap.add_argument('--size', type=int, default=256)
ap.add_argument('--out', default=str(ROOT / 'calib'))
ap.add_argument('--full', action='store_true', help='リサイズせず元解像度でも1枚保存（確認用）')
args = ap.parse_args()

out = Path(args.out)
out.mkdir(exist_ok=True)
cam = Camera(args.device)
for i in range(args.count):
    frame = cam.latest()
    if i == 0:
        print('camera frame', frame.shape)
        if args.full:
            cv2.imwrite(str(out.parent / 'snapshot_full.jpg'), frame)
    img = cv2.resize(frame, (args.size, args.size), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(out / f'calib_{i:03d}.jpg'), img, [cv2.IMWRITE_JPEG_QUALITY, 95])
    time.sleep(args.interval)
cam.stop()
print(f'saved {args.count} images to {out}')
