#!/usr/bin/env python3
"""カメラ1枚撮影 -> tools/snapshot.jpg (画角・明るさ確認用)"""
import glob, sys
for p in glob.glob("/usr/lib/python3*/dist-packages"):
    sys.path.insert(0, p)
import cv2
dev = sys.argv[1] if len(sys.argv) > 1 else "/dev/video0"
cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280); cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
for _ in range(15):
    ok, frame = cap.read()
print("ok", ok, None if frame is None else frame.shape, "mean", None if frame is None else frame.mean())
cv2.imwrite(__file__.rsplit("/", 1)[0] + "/snapshot.jpg", frame)
