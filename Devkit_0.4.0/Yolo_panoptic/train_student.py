#!/usr/bin/env python3
"""
Step 2-3: 生徒モデル(軽量CNN)の学習
  擬似ラベル(make_pseudo_labels.py の出力)で MobileNetV3 系セグメンテーションを 5クラス学習する。

  クラス: 0 other / 1 wall / 2 floor / 3 ceiling / 4 door / 255 ignore

実行例:
  python train_student.py                         # LRASPP-MobileNetV3, 384x512
  python train_student.py --arch deeplabv3        # DeepLabV3-MobileNetV3
  python train_student.py --epochs 80 --size 480 640

出力 (runs/<arch>_<timestamp>/):
  best.pt / last.pt         チェックポイント
  log.json                  エポックごとの loss / IoU
  val_preview/*.jpg         [入力 | 教師ラベル | 生徒予測] の比較画像
"""
import argparse
import json
import os
import random
import time

import cv2
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset
from torchvision.models.segmentation import (
    DeepLabV3_MobileNet_V3_Large_Weights,
    LRASPP_MobileNet_V3_Large_Weights,
    deeplabv3_mobilenet_v3_large,
    lraspp_mobilenet_v3_large,
)

CLASSES = ["other", "wall", "floor", "ceiling", "door"]
NC = len(CLASSES)
IGNORE = 255
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)
COLORS = {0: (80, 80, 80), 1: (180, 130, 70), 2: (60, 160, 60),
          3: (200, 200, 120), 4: (40, 90, 220), 255: (0, 0, 0)}


# ---------------------------------------------------------------------------
# データセット
# ---------------------------------------------------------------------------
def random_crop_pad(img, lbl, H, W):
    h, w = lbl.shape
    ph, pw = max(H - h, 0), max(W - w, 0)
    if ph or pw:
        img = cv2.copyMakeBorder(img, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        lbl = cv2.copyMakeBorder(lbl, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=IGNORE)
        h, w = lbl.shape
    y = random.randint(0, h - H)
    x = random.randint(0, w - W)
    return img[y:y + H, x:x + W], lbl[y:y + H, x:x + W]


class SegDataset(Dataset):
    def __init__(self, root, list_file, size, train):
        with open(os.path.join(root, list_file)) as f:
            self.items = [l.strip() for l in f if l.strip()]
        self.root, self.size, self.train = root, size, train

    def __len__(self):
        return len(self.items)

    def load(self, i):
        rel = self.items[i]
        img = cv2.imread(os.path.join(self.root, "images", rel))
        lbl = cv2.imread(os.path.join(self.root, "labels", os.path.splitext(rel)[0] + ".png"),
                         cv2.IMREAD_UNCHANGED)
        return img, lbl

    def __getitem__(self, i):
        img, lbl = self.load(i)
        H, W = self.size
        if self.train:
            s = random.uniform(0.75, 1.25)
            sw, sh = int(W * s), int(H * s)
            img = cv2.resize(img, (sw, sh), interpolation=cv2.INTER_LINEAR)
            lbl = cv2.resize(lbl, (sw, sh), interpolation=cv2.INTER_NEAREST)
            img, lbl = random_crop_pad(img, lbl, H, W)
            if random.random() < 0.5:
                img, lbl = img[:, ::-1], lbl[:, ::-1]
            # 照明変化(明るさ・コントラスト・ガンマ)
            f = img.astype(np.float32) / 255.0
            f = np.clip(f * random.uniform(0.6, 1.4) + random.uniform(-0.12, 0.12), 0, 1)
            f = f ** random.uniform(0.7, 1.4)
            img = (f * 255).astype(np.uint8)
        else:
            img = cv2.resize(img, (W, H), interpolation=cv2.INTER_LINEAR)
            lbl = cv2.resize(lbl, (W, H), interpolation=cv2.INTER_NEAREST)
        return to_tensor(img), torch.from_numpy(np.ascontiguousarray(lbl)).long()


def to_tensor(bgr):
    rgb = bgr[:, :, ::-1].astype(np.float32) / 255.0
    rgb = (rgb - MEAN) / STD
    return torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1)))


def compute_class_weights(ds, max_w=5.0):
    counts = np.zeros(NC, np.int64)
    for i in range(len(ds)):
        _, lbl = ds.load(i)
        counts += np.bincount(lbl[lbl != IGNORE].ravel(), minlength=NC)[:NC]
    freq = counts / counts.sum()
    w = 1.0 / np.sqrt(freq + 1e-6)
    w = np.clip(w / w.mean(), 0.2, max_w)
    for c in range(NC):
        print(f"[weight] {CLASSES[c]:8s} freq {100 * freq[c]:5.1f}%  w {w[c]:.2f}")
    return torch.tensor(w, dtype=torch.float32)


# ---------------------------------------------------------------------------
# モデル
# ---------------------------------------------------------------------------
def build_model(arch, pretrained=True):
    if arch == "lraspp":
        m = lraspp_mobilenet_v3_large(
            weights=LRASPP_MobileNet_V3_Large_Weights.DEFAULT if pretrained else None)
        m.classifier.low_classifier = nn.Conv2d(m.classifier.low_classifier.in_channels, NC, 1)
        m.classifier.high_classifier = nn.Conv2d(m.classifier.high_classifier.in_channels, NC, 1)
    elif arch == "deeplabv3":
        m = deeplabv3_mobilenet_v3_large(
            weights=DeepLabV3_MobileNet_V3_Large_Weights.DEFAULT if pretrained else None)
        m.classifier[4] = nn.Conv2d(m.classifier[4].in_channels, NC, 1)
        m.aux_classifier = None
    else:
        raise ValueError(arch)
    return m


# ---------------------------------------------------------------------------
# 評価
# ---------------------------------------------------------------------------
@torch.no_grad()
def evaluate(model, loader, device):
    model.eval()
    conf = torch.zeros(NC, NC, dtype=torch.long)
    for x, y in loader:
        pred = model(x.to(device))["out"].argmax(1).cpu()
        m = y != IGNORE
        conf += torch.bincount(y[m] * NC + pred[m], minlength=NC * NC).view(NC, NC)
    tp = conf.diag().float()
    denom = conf.sum(0).float() + conf.sum(1).float() - tp
    iou = torch.where(denom > 0, tp / denom, torch.full_like(tp, float("nan")))
    return iou.numpy()


def colorize(label):
    out = np.zeros((*label.shape, 3), np.uint8)
    for k, c in COLORS.items():
        out[label == k] = c
    return out


@torch.no_grad()
def save_val_preview(model, ds, out_dir, device):
    os.makedirs(out_dir, exist_ok=True)
    model.eval()
    H, W = ds.size
    for i, rel in enumerate(ds.items):
        img, lbl = ds.load(i)
        img = cv2.resize(img, (W, H))
        lbl = cv2.resize(lbl, (W, H), interpolation=cv2.INTER_NEAREST)
        pred = model(to_tensor(img)[None].to(device))["out"].argmax(1)[0].cpu().numpy()
        t = cv2.addWeighted(img, 0.5, colorize(lbl), 0.5, 0)
        s = cv2.addWeighted(img, 0.5, colorize(pred.astype(np.uint8)), 0.5, 0)
        for im, txt in ((img, "input"), (t, "teacher"), (s, "student")):
            cv2.putText(im, txt, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        name = rel.replace("/", "_").replace("\\", "_")
        cv2.imwrite(os.path.join(out_dir, os.path.splitext(name)[0] + ".jpg"),
                    np.hstack([img, t, s]))


@torch.no_grad()
def benchmark(model, size, device, n=20):
    model.eval()
    x = torch.randn(1, 3, *size, device=device)
    for _ in range(3):
        model(x)
    t = time.perf_counter()
    for _ in range(n):
        model(x)
    return (time.perf_counter() - t) / n * 1000


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="dataset")
    ap.add_argument("--arch", choices=["lraspp", "deeplabv3"], default="lraspp")
    ap.add_argument("--size", type=int, nargs=2, default=[384, 512], metavar=("H", "W"))
    ap.add_argument("--epochs", type=int, default=60)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=5e-4)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--eval-every", type=int, default=5)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    size = tuple(args.size)

    run_dir = os.path.join("runs", f"{args.arch}_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(run_dir, exist_ok=True)
    print(f"[info] device={device}  arch={args.arch}  size={size}  -> {run_dir}")

    train_ds = SegDataset(args.root, "train.txt", size, train=True)
    val_ds = SegDataset(args.root, "val.txt", size, train=False)
    print(f"[info] train {len(train_ds)} / val {len(val_ds)}")
    train_dl = DataLoader(train_ds, batch_size=args.batch, shuffle=True,
                          num_workers=args.workers, drop_last=True)
    val_dl = DataLoader(val_ds, batch_size=args.batch, shuffle=False, num_workers=args.workers)

    weights = compute_class_weights(train_ds).to(device)
    criterion = nn.CrossEntropyLoss(weight=weights, ignore_index=IGNORE)

    model = build_model(args.arch).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)

    best_miou, log = -1.0, []
    ckpt_meta = dict(arch=args.arch, size=size, classes=CLASSES,
                     mean=MEAN.tolist(), std=STD.tolist())

    for ep in range(1, args.epochs + 1):
        model.train()
        t0, total = time.time(), 0.0
        for x, y in train_dl:
            x, y = x.to(device), y.to(device)
            loss = criterion(model(x)["out"], y)
            opt.zero_grad()
            loss.backward()
            opt.step()
            total += loss.item()
        sched.step()
        avg = total / max(len(train_dl), 1)
        entry = dict(epoch=ep, loss=avg, lr=sched.get_last_lr()[0], sec=time.time() - t0)

        if ep % args.eval_every == 0 or ep == args.epochs:
            iou = evaluate(model, val_dl, device)
            miou = float(np.nanmean(iou))
            entry.update(miou=miou, iou={c: float(v) for c, v in zip(CLASSES, iou)})
            iou_str = " ".join(f"{c}:{v:.2f}" for c, v in zip(CLASSES, iou))
            print(f"[ep {ep:3d}] loss {avg:.4f}  mIoU {miou:.3f}  ({iou_str})  {entry['sec']:.0f}s")
            if miou > best_miou:
                best_miou = miou
                torch.save(dict(model=model.state_dict(), epoch=ep, miou=miou, **ckpt_meta),
                           os.path.join(run_dir, "best.pt"))
                print(f"          -> best updated")
        else:
            print(f"[ep {ep:3d}] loss {avg:.4f}  {entry['sec']:.0f}s")

        log.append(entry)
        with open(os.path.join(run_dir, "log.json"), "w") as f:
            json.dump(log, f, indent=1)

    torch.save(dict(model=model.state_dict(), epoch=args.epochs, **ckpt_meta),
               os.path.join(run_dir, "last.pt"))

    best = torch.load(os.path.join(run_dir, "best.pt"), map_location=device)
    model.load_state_dict(best["model"])
    save_val_preview(model, val_ds, os.path.join(run_dir, "val_preview"), device)
    ms = benchmark(model, size, device)
    print(f"\n[done] best mIoU {best['miou']:.3f} (epoch {best['epoch']})")
    print(f"[bench] {args.arch} {size[1]}x{size[0]} on {device}: {ms:.1f} ms/frame")
    print(f"[out] {run_dir}")


if __name__ == "__main__":
    main()
