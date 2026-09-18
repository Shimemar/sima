#!/usr/bin/env python3
"""
webcamから画像を取り込んで-90度回転させて表示する。

使い方:
    python webcam_rotate.py              # 使えるカメラを一覧表示して選ぶ
    python webcam_rotate.py --device 1   # デバイス番号を指定してスキップ
    python webcam_rotate.py --list       # カメラ一覧だけ表示して終了

'q' キーで終了。
"""

import argparse
import sys
from pathlib import Path

import cv2


def get_device_name(index: int) -> str:
    """Linuxの場合、/sys/class/video4linuxからカメラ名を取得する。取れなければ空文字。"""
    name_path = Path(f"/sys/class/video4linux/video{index}/name")
    if name_path.exists():
        try:
            return name_path.read_text().strip()
        except OSError:
            return ""
    return ""


def list_cameras(max_index: int = 10) -> list:
    """0〜max_index-1のデバイス番号を試して、開けたカメラの情報一覧を返す。"""
    cameras = []
    for index in range(max_index):
        cap = cv2.VideoCapture(index)
        if cap.isOpened():
            ok, _ = cap.read()
            if ok:
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                name = get_device_name(index)
                cameras.append({"index": index, "name": name, "width": width, "height": height})
        cap.release()
    return cameras


def choose_camera_interactively() -> int:
    print("カメラを検索中...")
    cameras = list_cameras()

    if not cameras:
        print("[ERROR] 使えるカメラが見つからなかった", file=sys.stderr)
        sys.exit(1)

    print("\n使えるカメラ:")
    for cam in cameras:
        label = cam["name"] if cam["name"] else "(名前取得不可)"
        print(f"  [{cam['index']}] {label}  {cam['width']}x{cam['height']}")

    valid_indices = {cam["index"] for cam in cameras}
    while True:
        choice = input("\n使うカメラの番号を入力: ").strip()
        if choice.isdigit() and int(choice) in valid_indices:
            return int(choice)
        print(f"[!] {list(valid_indices)} の中から選んでください")


def main() -> int:
    parser = argparse.ArgumentParser(description="webcam映像を-90度回転して表示")
    parser.add_argument("--device", type=int, default=None, help="カメラのデバイス番号(指定すると選択をスキップ)")
    parser.add_argument("--list", action="store_true", help="使えるカメラ一覧だけ表示して終了する")
    args = parser.parse_args()

    if args.list:
        cameras = list_cameras()
        if not cameras:
            print("使えるカメラが見つからなかった")
            return 1
        for cam in cameras:
            label = cam["name"] if cam["name"] else "(名前取得不可)"
            print(f"[{cam['index']}] {label}  {cam['width']}x{cam['height']}")
        return 0

    device = args.device if args.device is not None else choose_camera_interactively()

    cap = cv2.VideoCapture(device)
    if not cap.isOpened():
        print(f"[ERROR] カメラ(device={device})を開けなかった", file=sys.stderr)
        return 1

    print(f"device={device} を表示中... 'q' キーで終了")
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                print("[ERROR] フレームの取得に失敗した", file=sys.stderr)
                break

            # -90度回転 = 時計回りに90度回転
            rotated = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)

            cv2.imshow("webcam (-90 deg)", rotated)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
