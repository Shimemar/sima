#!/usr/bin/env python3
"""
Step 2-2: 教師モデル(ADE20K)による擬似ラベル生成
  ADE20Kの150クラス確率を 5クラスに集約し、自信の低い画素は ignore(255) にする。

  学習クラス:
    0: other   (家具・物・窓・白飛びなど 上記以外すべて)
    1: wall
    2: floor   (rug / carpet 含む)
    3: ceiling
    4: door
    255: ignore (教師の最大確率 < --conf-th)

実行例:
  python make_pseudo_labels.py                       # dataset/images 以下を全部処理
  python make_pseudo_labels.py --tta --conf-th 0.6   # 左右反転TTAで品質優先

出力:
  dataset/labels/<room>/<name>.png    ラベル (uint8)
  dataset/preview/<room>/<name>.jpg   確認用の色付き画像
  dataset/train.txt, dataset/val.txt  画像の相対パス一覧
  ※ 既にラベルがある画像はスキップ(中断しても再開可)
"""
import argparse
import glob
import hashlib
import os
import time

import cv2
import numpy as np
import torch
import torch.nn.functional as F
from transformers import AutoImageProcessor, AutoModelForSemanticSegmentation

CLASSES = ["other", "wall", "floor", "ceiling", "door"]
GROUPS = {  # 学習クラスID: 対応するADE20Kラベル名
    1: ["wall"],
    2: ["floor", "flooring", "rug", "carpet", "carpeting"],
    3: ["ceiling"],
    4: ["door", "double door", "screen door"],
}
COLORS = {  # BGR
    0: (80, 80, 80), 1: (180, 130, 70), 2: (60, 160, 60),
    3: (200, 200, 120), 4: (40, 90, 220), 255: (0, 0, 0),
}
IGNORE = 255


def build_group_matrix(id2label, num_labels):
    """(num_ade, 5) の集約行列。どのグループにも属さないADEクラスは other(0) へ"""
    M = torch.zeros(num_labels, len(CLASSES))
    for idx in range(num_labels):
        name = id2label[idx]
        tokens = [t.strip().lower() for t in name.replace(",", ";").split(";")]
        g = 0
        for gid, cands in GROUPS.items():
            if any(t in cands for t in tokens):
                g = gid
                break
        M[idx, g] = 1.0
    for gid in GROUPS:
        src = [id2label[i] for i in range(num_labels) if M[i, gid] > 0]
        print(f"[map] {CLASSES[gid]:8s} <- {src if src else '見つかりません'}")
    return M


class Teacher:
    def __init__(self, model_name, device):
        print(f"[teacher] loading {model_name} on {device}")
        self.proc = AutoImageProcessor.from_pretrained(model_name)
        self.model = AutoModelForSemanticSegmentation.from_pretrained(model_name).to(device).eval()
        self.device = device
        cfg = self.model.config
        self.M = build_group_matrix(cfg.id2label, cfg.num_labels).to(device)

    @torch.no_grad()
    def _probs(self, rgb):
        inputs = self.proc(images=rgb, return_tensors="pt").to(self.device)
        return self.model(**inputs).logits[0].softmax(0)       # (150, h, w)

    @torch.no_grad()
    def __call__(self, bgr, tta=False):
        H, W = bgr.shape[:2]
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        p = self._probs(rgb)
        if tta:
            p = 0.5 * (p + self._probs(rgb[:, ::-1].copy()).flip(-1))
        C, h, w = p.shape
        g = (self.M.T @ p.reshape(C, -1)).reshape(len(CLASSES), h, w)   # (5, h, w)
        g = F.interpolate(g[None], size=(H, W), mode="bilinear", align_corners=False)[0]
        conf, label = g.max(0)
        return label.to(torch.uint8).cpu().numpy(), conf.cpu().numpy()


def colorize(label):
    out = np.zeros((*label.shape, 3), np.uint8)
    for k, c in COLORS.items():
        out[label == k] = c
    return out


def is_val(rel_path, ratio):
    h = int(hashlib.md5(rel_path.encode()).hexdigest(), 16)
    return (h % 1000) < ratio * 1000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--teacher", default="openmmlab/upernet-convnext-small")
    ap.add_argument("--conf-th", type=float, default=0.5)
    ap.add_argument("--tta", action="store_true", help="左右反転TTA (約2倍の時間)")
    ap.add_argument("--val-ratio", type=float, default=0.1)
    ap.add_argument("--no-preview", action="store_true")
    args = ap.parse_args()

    img_root = os.path.join(args.root, "images")
    lbl_root = os.path.join(args.root, "labels")
    prv_root = os.path.join(args.root, "preview")

    paths = sorted(glob.glob(os.path.join(img_root, "**", "*.jpg"), recursive=True))
    if not paths:
        raise SystemExit(f"{img_root} に画像がありません")
    print(f"[info] {len(paths)} images")

    device = "cuda" if torch.cuda.is_available() else "cpu"
    teacher = Teacher(args.teacher, device)

    counts = np.zeros(256, np.int64)
    train, val = [], []
    t_start = time.time()
    done = 0

    for i, p in enumerate(paths):
        rel = os.path.relpath(p, img_root)
        stem = os.path.splitext(rel)[0]
        lbl_path = os.path.join(lbl_root, stem + ".png")
        (val if is_val(rel, args.val_ratio) else train).append(rel)

        if os.path.exists(lbl_path):
            label = cv2.imread(lbl_path, cv2.IMREAD_UNCHANGED)
        else:
            img = cv2.imread(p)
            label, conf = teacher(img, tta=args.tta)
            label[conf < args.conf_th] = IGNORE
            os.makedirs(os.path.dirname(lbl_path), exist_ok=True)
            cv2.imwrite(lbl_path, label)
            if not args.no_preview:
                prv_path = os.path.join(prv_root, stem + ".jpg")
                os.makedirs(os.path.dirname(prv_path), exist_ok=True)
                vis = cv2.addWeighted(img, 0.5, colorize(label), 0.5, 0)
                cv2.imwrite(prv_path, np.hstack([img, vis]))
            done += 1

        counts += np.bincount(label.ravel(), minlength=256)
        if (i + 1) % 20 == 0 or i + 1 == len(paths):
            el = time.time() - t_start
            eta = el / max(done, 1) * (len(paths) - i - 1) if done else 0
            print(f"[{i + 1}/{len(paths)}] {el / 60:5.1f} min  ETA {eta / 60:5.1f} min")

    with open(os.path.join(args.root, "train.txt"), "w") as f:
        f.write("\n".join(train) + "\n")
    with open(os.path.join(args.root, "val.txt"), "w") as f:
        f.write("\n".join(val) + "\n")

    total = counts.sum()
    print("\n[stats] 画素比率")
    for k in list(range(len(CLASSES))) + [IGNORE]:
        name = CLASSES[k] if k < len(CLASSES) else "ignore"
        print(f"  {k:3d} {name:8s} {100 * counts[k] / total:5.1f}%")
    print(f"[split] train {len(train)} / val {len(val)}")


if __name__ == "__main__":
    main()
