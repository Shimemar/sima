#!/usr/bin/env python3
"""
Step 2-1: 蒸留用データセットの録画
  webcamから一定間隔でフレームを保存する。ブレた画像は自動で除外。

実行例:
  python record_frames.py --room hallway
  python record_frames.py --room living --interval 0.5 --mjpg

キー操作:
  r       : 録画 開始/停止
  q / ESC : 終了

保存先:
  dataset/images/<room>/<room>_<timestamp>_<連番>.jpg
"""
import argparse
import os
import time

import cv2


def list_cameras(max_index=10):
    """番号 0..max_index-1 を順に開いて、映像が取れるカメラを表示する"""
    by_path = {}
    if os.path.isdir("/dev/v4l/by-id"):  # Linux: 番号とデバイス名の対応
        for name in sorted(os.listdir("/dev/v4l/by-id")):
            real = os.path.realpath(os.path.join("/dev/v4l/by-id", name))
            by_path[real] = name
    print("[cameras]")
    for i in range(max_index):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok:
                h, w = frame.shape[:2]
                dev = f"/dev/video{i}"
                print(f"  --camera {i:<2d} {w}x{h}  {dev}  {by_path.get(dev, '')}")
        cap.release()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--room", help="部屋名 (保存サブフォルダ名)")
    ap.add_argument("--out", default="dataset/images")
    ap.add_argument("--camera", default="0",
                    help="カメラ番号 (0,1,...) またはデバイスパス (/dev/video2 など)")
    ap.add_argument("--list-cameras", action="store_true",
                    help="接続中のカメラを一覧表示して終了")
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    ap.add_argument("--mjpg", action="store_true")
    ap.add_argument("--interval", type=float, default=0.5, help="保存間隔[秒]")
    ap.add_argument("--blur-th", type=float, default=60.0,
                    help="ラプラシアン分散がこれ未満のフレームはブレとして除外")
    args = ap.parse_args()

    if args.list_cameras:
        list_cameras()
        return
    if not args.room:
        ap.error("--room を指定してください")

    out_dir = os.path.join(args.out, args.room)
    os.makedirs(out_dir, exist_ok=True)

    src = int(args.camera) if args.camera.isdigit() else args.camera
    print(f"[info] camera = {src}")
    cap = cv2.VideoCapture(src)
    if args.mjpg:
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
    if not cap.isOpened():
        raise SystemExit(f"カメラ {args.camera} を開けません")

    session = time.strftime("%Y%m%d_%H%M%S")
    recording, saved, skipped_blur = False, 0, 0
    last_save = 0.0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        now = time.time()

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp = cv2.Laplacian(gray, cv2.CV_64F).var()

        if recording and now - last_save >= args.interval:
            if sharp >= args.blur_th:
                path = os.path.join(out_dir, f"{args.room}_{session}_{saved:05d}.jpg")
                cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
                saved += 1
            else:
                skipped_blur += 1
            last_save = now

        vis = frame.copy()
        status = "REC" if recording else "STOP"
        color = (0, 0, 255) if recording else (200, 200, 200)
        cv2.putText(vis, f"[{status}] {args.room} saved {saved}  blur-skip {skipped_blur}",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2, cv2.LINE_AA)
        cv2.putText(vis, f"sharpness {sharp:6.1f} (th {args.blur_th})",
                    (8, 46), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 255, 0) if sharp >= args.blur_th else (0, 0, 255), 1, cv2.LINE_AA)
        if recording:
            cv2.circle(vis, (vis.shape[1] - 20, 20), 8, (0, 0, 255), -1)

        cv2.imshow("record (r:rec q:quit)", vis)
        key = cv2.waitKey(1) & 0xFF
        if key in (ord("q"), 27):
            break
        if key == ord("r"):
            recording = not recording
            print(f"[rec] {'start' if recording else 'stop'}  saved={saved}")

    cap.release()
    cv2.destroyAllWindows()
    print(f"[done] {saved} frames -> {out_dir}  (blur skipped {skipped_blur})")


if __name__ == "__main__":
    main()
